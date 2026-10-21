import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
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
from .config import get_settings
from .contracts import TurnRequest, TurnResponse, UserPreferences
from .database import get_catalog
from .orchestrator import handle_turn
from .security import InvalidTokenError, decode_access_token


WEB_ROOT = Path(__file__).with_name("web")
BEARER = HTTPBearer(auto_error=False)


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
    app = FastAPI(title=settings.app_name, version="0.1.0")
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

    @app.post("/v1/auth/register", response_model=AuthResponse, status_code=201)
    async def register(credentials: Credentials) -> AuthResponse:
        try:
            return await asyncio.to_thread(AuthService().register, credentials)
        except UsernameTakenError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/auth/login", response_model=AuthResponse)
    async def login(credentials: Credentials) -> AuthResponse:
        try:
            return await asyncio.to_thread(AuthService().login, credentials)
        except InvalidCredentialsError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.post("/v1/turns", response_model=TurnResponse)
    async def create_turn(
        request: TurnRequest,
        user: Annotated[PublicUser, Depends(require_user)],
    ) -> TurnResponse:
        try:
            return await handle_turn(
                request.message,
                request.conversation_id,
                user_id=user.user_id,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

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

