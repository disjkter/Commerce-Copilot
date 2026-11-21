import asyncio
import hashlib
import ipaddress
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from time import perf_counter
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import ValidationError
from redis.exceptions import RedisError
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from .auth import (
    AuthResponse,
    AuthService,
    Credentials,
    InvalidCredentialsError,
    PublicUser,
    UsernameTakenError,
)
from .config import Settings, get_settings
from .contracts import MemoryItem, TurnRequest, TurnResponse, TurnTask, UserPreferences
from .database import get_catalog
from .logistics import verify_webhook_signature
from .metrics import record_rate_limit, render_metrics
from .observability import log_event
from .orchestrator import handle_turn
from .rate_limit import InMemorySlidingWindowRateLimiter, RateLimiter
from .retrieval import get_retriever
from .security import InvalidTokenError, decode_access_token
from .support import LogisticsWebhookResponse, TrackingWebhookPayload
from .task_queue import RedisTurnTaskQueue
from .turn_tasks import TurnTaskScheduler, task_can_be_scheduled


WEB_ROOT = Path(__file__).with_name("web")
BEARER = HTTPBearer(auto_error=False)
LOGGER = logging.getLogger("commerce_copilot.api")


def _iter_answer_chunks(answer: str, *, max_chars: int = 24) -> list[str]:
    """Split a validated answer into stable, display-friendly SSE deltas."""

    chunks: list[str] = []
    start = 0
    boundaries = "，。！？；,.!?;\n"
    for index, character in enumerate(answer):
        length = index - start + 1
        if character in boundaries or length >= max_chars:
            chunks.append(answer[start : index + 1])
            start = index + 1
    if start < len(answer):
        chunks.append(answer[start:])
    return chunks


def _client_identity(request: Request, settings: Settings) -> str:
    direct = request.client.host if request.client is not None else "unknown"
    if not settings.trust_proxy_headers:
        return direct
    forwarded = request.headers.get("X-Forwarded-For", "").split(",", maxsplit=1)[0].strip()
    try:
        return str(ipaddress.ip_address(forwarded))
    except ValueError:
        return direct


def _enforce_rate_limit(
    limiter: RateLimiter,
    *,
    enabled: bool,
    scope: str,
    identity: str,
    limit: int,
    window_seconds: int,
) -> None:
    if not enabled:
        return
    identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    decision = limiter.check(
        f"{scope}:{identity_hash}",
        limit=limit,
        window_seconds=window_seconds,
    )
    try:
        record_rate_limit(scope, decision.allowed)
    except Exception as metrics_error:
        log_event(
            LOGGER,
            "metrics_recording_failed",
            level=logging.WARNING,
            error_type=type(metrics_error).__name__,
            metric_scope="rate_limit",
        )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many requests; please retry later",
            headers={
                "Retry-After": str(decision.retry_after_seconds),
                "X-RateLimit-Limit": str(decision.limit),
                "X-RateLimit-Remaining": "0",
            },
        )


def require_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(BEARER)],
) -> PublicUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authentication required",
        )
    try:
        user_id = decode_access_token(
            credentials.credentials,
            get_settings().auth_secret,
        )
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or expired token",
        ) from exc
    user = get_catalog().get_user_by_id(user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="user no longer exists",
        )
    return PublicUser(user_id=user.user_id, username=user.username)


def create_app() -> FastAPI:
    settings = get_settings()
    turn_task_scheduler = TurnTaskScheduler(
        worker_id="api-fallback",
        lease_seconds=settings.task_lease_seconds,
    )
    turn_task_queue = (
        RedisTurnTaskQueue(
            settings.redis_url,
            stream_key=settings.task_stream_key,
            consumer_group=settings.task_consumer_group,
        )
        if settings.task_queue_backend == "redis"
        else None
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if (
            settings.embedding_warmup_enabled
            and settings.embedding_provider.casefold() == "bge"
        ):
            started = perf_counter()
            try:
                await asyncio.to_thread(get_retriever().warmup)
                log_event(
                    LOGGER,
                    "embedding_warmup_completed",
                    elapsed_ms=round((perf_counter() - started) * 1000, 2),
                    model=settings.embedding_model,
                    device=settings.embedding_device,
                )
            except Exception as exc:
                log_event(
                    LOGGER,
                    "embedding_warmup_failed",
                    level=logging.WARNING,
                    elapsed_ms=round((perf_counter() - started) * 1000, 2),
                    error_type=type(exc).__name__,
                )
        try:
            yield
        finally:
            await turn_task_scheduler.close()
            if turn_task_queue is not None:
                await turn_task_queue.close()

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    app.state.rate_limiter = InMemorySlidingWindowRateLimiter()
    app.state.turn_task_scheduler = turn_task_scheduler
    app.state.turn_task_queue = turn_task_queue
    app.mount("/assets", StaticFiles(directory=WEB_ROOT), name="assets")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(
            WEB_ROOT / "index.html",
            headers={"Cache-Control": "no-store, max-age=0"},
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(
            content=render_metrics(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.post(
        "/v1/webhooks/logistics/{provider}",
        response_model=LogisticsWebhookResponse,
    )
    async def receive_logistics_webhook(
        provider: str,
        request: Request,
    ) -> LogisticsWebhookResponse:
        normalized_provider = provider.strip().upper()
        if not normalized_provider or len(normalized_provider) > 64:
            raise HTTPException(status_code=400, detail="invalid logistics provider")
        _enforce_rate_limit(
            app.state.rate_limiter,
            enabled=settings.rate_limit_enabled,
            scope="logistics_webhook",
            identity=f"{normalized_provider}:{_client_identity(request, settings)}",
            limit=settings.webhook_rate_limit,
            window_seconds=settings.webhook_rate_window_seconds,
        )
        raw_body = await request.body()
        if not verify_webhook_signature(
            raw_body,
            request.headers.get("X-Logistics-Signature"),
            settings.logistics_webhook_secret,
        ):
            raise HTTPException(status_code=401, detail="invalid webhook signature")
        try:
            event = TrackingWebhookPayload.model_validate_json(raw_body)
        except ValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail=exc.errors(include_url=False),
            ) from exc
        result = await asyncio.to_thread(
            get_catalog().ingest_tracking_event,
            normalized_provider,
            event,
        )
        return LogisticsWebhookResponse(
            provider=normalized_provider,
            provider_event_id=event.provider_event_id,
            result=result,
        )

    @app.post("/v1/auth/register", response_model=AuthResponse, status_code=201)
    async def register(request: Request, credentials: Credentials) -> AuthResponse:
        _enforce_rate_limit(
            app.state.rate_limiter,
            enabled=settings.rate_limit_enabled,
            scope="register",
            identity=_client_identity(request, settings),
            limit=settings.register_rate_limit,
            window_seconds=settings.register_rate_window_seconds,
        )
        try:
            return await asyncio.to_thread(AuthService().register, credentials)
        except UsernameTakenError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/auth/login", response_model=AuthResponse)
    async def login(request: Request, credentials: Credentials) -> AuthResponse:
        _enforce_rate_limit(
            app.state.rate_limiter,
            enabled=settings.rate_limit_enabled,
            scope="login",
            identity=_client_identity(request, settings),
            limit=settings.login_rate_limit,
            window_seconds=settings.login_rate_window_seconds,
        )
        try:
            return await asyncio.to_thread(AuthService().login, credentials)
        except InvalidCredentialsError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.post("/v1/turns", response_model=TurnResponse)
    async def create_turn(
        request: Request,
        turn: TurnRequest,
        user: Annotated[PublicUser, Depends(require_user)],
    ) -> TurnResponse:
        _enforce_rate_limit(
            app.state.rate_limiter,
            enabled=settings.rate_limit_enabled,
            scope="turn",
            identity=user.user_id,
            limit=settings.turn_rate_limit,
            window_seconds=settings.turn_rate_window_seconds,
        )
        try:
            return await handle_turn(
                turn.message,
                turn.conversation_id,
                user_id=user.user_id,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

    @app.post(
        "/v1/turn-tasks",
        response_model=TurnTask,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_turn_task(
        request: Request,
        turn: TurnRequest,
        user: Annotated[PublicUser, Depends(require_user)],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> TurnTask:
        """Accept work immediately and execute it outside the request lifecycle."""

        _enforce_rate_limit(
            app.state.rate_limiter,
            enabled=settings.rate_limit_enabled,
            scope="turn",
            identity=user.user_id,
            limit=settings.turn_rate_limit,
            window_seconds=settings.turn_rate_window_seconds,
        )
        if idempotency_key is not None and not 1 <= len(idempotency_key.strip()) <= 128:
            raise HTTPException(status_code=400, detail="invalid Idempotency-Key")
        task, _ = await asyncio.to_thread(
            get_catalog().create_turn_task,
            user.user_id,
            turn,
            idempotency_key=idempotency_key,
        )
        if task_can_be_scheduled(task):
            if app.state.turn_task_queue is not None:
                try:
                    await app.state.turn_task_queue.enqueue(
                        task.task_id,
                        user_id=user.user_id,
                    )
                except (RedisError, OSError) as exc:
                    log_event(
                        LOGGER,
                        "turn_task_queue_fallback",
                        level=logging.WARNING,
                        task_id=task.task_id,
                        error_type=type(exc).__name__,
                    )
                    app.state.turn_task_scheduler.submit(
                        task.task_id,
                        user_id=user.user_id,
                    )
            else:
                app.state.turn_task_scheduler.submit(
                    task.task_id,
                    user_id=user.user_id,
                )
        return task

    @app.get("/v1/turn-tasks/{task_id}", response_model=TurnTask)
    async def get_turn_task(
        task_id: str,
        user: Annotated[PublicUser, Depends(require_user)],
    ) -> TurnTask:
        task = await asyncio.to_thread(
            get_catalog().get_turn_task,
            task_id,
            user_id=user.user_id,
        )
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return task

    @app.delete("/v1/turn-tasks/{task_id}", response_model=TurnTask)
    async def cancel_turn_task(
        task_id: str,
        user: Annotated[PublicUser, Depends(require_user)],
    ) -> TurnTask:
        task = await asyncio.to_thread(
            get_catalog().get_turn_task,
            task_id,
            user_id=user.user_id,
        )
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        cancelled = await asyncio.to_thread(
            get_catalog().cancel_turn_task,
            task_id,
            user_id=user.user_id,
        )
        if not cancelled:
            raise HTTPException(
                status_code=409,
                detail="only pending tasks can be cancelled",
            )
        refreshed = await asyncio.to_thread(
            get_catalog().get_turn_task,
            task_id,
            user_id=user.user_id,
        )
        if refreshed is None:  # Defensive: ownership cannot disappear mid-transaction.
            raise HTTPException(status_code=404, detail="task not found")
        return refreshed

    @app.get("/v1/memories", response_model=list[MemoryItem])
    async def list_memories(
        user: Annotated[PublicUser, Depends(require_user)],
        conversation_id: str | None = None,
        include_invalidated: bool = False,
    ) -> list[MemoryItem]:
        """List the authenticated user's inspectable long-term memories."""

        return await asyncio.to_thread(
            get_catalog().list_memory_items,
            user_id=user.user_id,
            conversation_id=conversation_id,
            include_invalidated=include_invalidated,
        )

    @app.delete("/v1/memories/{memory_id}", status_code=204)
    async def forget_memory(
        memory_id: str,
        user: Annotated[PublicUser, Depends(require_user)],
    ) -> Response:
        """Invalidate a memory while preserving its provenance audit trail."""

        invalidated = await asyncio.to_thread(
            get_catalog().invalidate_memory_item,
            memory_id,
            user_id=user.user_id,
        )
        if not invalidated:
            raise HTTPException(status_code=404, detail="active memory not found")
        return Response(status_code=204)

    @app.post("/v1/turns/stream")
    async def stream_turn(
        request: Request,
        turn: TurnRequest,
        user: Annotated[PublicUser, Depends(require_user)],
    ) -> StreamingResponse:
        _enforce_rate_limit(
            app.state.rate_limiter,
            enabled=settings.rate_limit_enabled,
            scope="turn",
            identity=user.user_id,
            limit=settings.turn_rate_limit,
            window_seconds=settings.turn_rate_window_seconds,
        )
        async def event_stream() -> AsyncIterator[str]:
            queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()

            async def publish(event: dict[str, object]) -> None:
                if event.get("event") == "answer_ready":
                    answer = event.get("answer")
                    if not isinstance(answer, str):
                        return
                    trace_id = str(event.get("trace_id", ""))
                    source = str(event.get("source", "local"))
                    await queue.put(
                        {
                            "event": "answer_started",
                            "trace_id": trace_id,
                            "source": source,
                        }
                    )
                    for index, delta in enumerate(_iter_answer_chunks(answer)):
                        await queue.put(
                            {
                                "event": "answer_delta",
                                "trace_id": trace_id,
                                "index": index,
                                "delta": delta,
                            }
                        )
                        # Give the ASGI server an opportunity to flush each SSE frame.
                        await asyncio.sleep(0)
                    await queue.put(
                        {
                            "event": "answer_finished",
                            "trace_id": trace_id,
                            "characters": len(answer),
                        }
                    )
                    return
                await queue.put(event)

            async def run_workflow() -> None:
                try:
                    response = await handle_turn(
                        turn.message,
                        turn.conversation_id,
                        user_id=user.user_id,
                        event_sink=publish,
                    )
                    await queue.put(
                        {
                            "event": "complete",
                            "trace_id": response.trace.trace_id,
                            "response": response.model_dump(mode="json"),
                        }
                    )
                except PermissionError as exc:
                    await queue.put(
                        {"event": "error", "error": str(exc), "status_code": 403}
                    )
                except Exception as exc:
                    await queue.put(
                        {
                            "event": "error",
                            "error": type(exc).__name__,
                            "status_code": 500,
                        }
                    )
                finally:
                    await queue.put(None)

            task = asyncio.create_task(run_workflow())
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break
                    event_name = str(item.get("event", "message"))
                    data = json.dumps(item, ensure_ascii=False)
                    yield f"event: {event_name}\ndata: {data}\n\n"
            finally:
                if not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/v1/me/preferences", response_model=UserPreferences)
    async def get_preferences(
        user: Annotated[PublicUser, Depends(require_user)],
    ) -> UserPreferences:
        return await asyncio.to_thread(
            get_catalog().get_user_preferences,
            user.user_id,
        )

    @app.put("/v1/me/preferences", response_model=UserPreferences)
    async def update_preferences(
        preferences: UserPreferences,
        user: Annotated[PublicUser, Depends(require_user)],
    ) -> UserPreferences:
        return await asyncio.to_thread(
            get_catalog().save_user_preferences,
            user.user_id,
            preferences,
        )

    return app


app = create_app()

