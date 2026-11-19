import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from enum import StrEnum
from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, ValidationError

from .contracts import ToolCallTrace
from .observability import log_event
from .tool_sandbox import ToolPolicyError, ToolSandbox, ToolSandboxRequest


LOGGER = logging.getLogger("commerce_copilot.tools")


class ToolRiskLevel(StrEnum):
    READ = "read"
    LOW_WRITE = "low_write"
    HIGH_WRITE = "high_write"
    CRITICAL = "critical"


class ToolContext(BaseModel):
    user_id: str
    trace_id: str = ""
    conversation_id: str | None = None
    confirmation_token: str | None = None
    agent_role: str | None = None
    permissions: set[str] = Field(default_factory=set)
    confirmed_actions: set[str] = Field(default_factory=set)


class ToolExecutionResult(BaseModel):
    success: bool
    data: Any = None
    error_type: str | None = None
    retry_count: int = 0
    elapsed_ms: float = 0


ToolHandler = Callable[[BaseModel, ToolContext], Awaitable[Any]]


class ToolSpec(BaseModel):
    name: str
    display_name: str = ""
    description: str
    input_model: type[BaseModel]
    risk_level: ToolRiskLevel
    timeout_seconds: float = 3
    max_retries: int = 0
    allowed_roles: set[str] = Field(default_factory=set)
    required_permission: str | None = None
    requires_confirmation: bool = False
    handler: ToolHandler

    model_config = {"arbitrary_types_allowed": True}


class ToolExecutionError(RuntimeError):
    pass


_TOOL_CONTEXT: ContextVar[ToolContext | None] = ContextVar(
    "commerce_tool_context",
    default=None,
)
_TOOL_TRACES: ContextVar[list[ToolCallTrace] | None] = ContextVar(
    "commerce_tool_traces",
    default=None,
)


class ToolRuntime:
    def __init__(self, *, sandbox: ToolSandbox | None = None) -> None:
        self._registry: dict[str, ToolSpec] = {}
        self._sandbox = sandbox or ToolSandbox()

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._registry:
            raise ValueError(f"tool already registered: {spec.name}")
        if spec.risk_level != ToolRiskLevel.READ and spec.max_retries > 0:
            raise ValueError("write tools cannot enable blind runtime retries")
        if spec.risk_level != ToolRiskLevel.READ and not spec.required_permission:
            raise ValueError("write tools must declare a required permission")
        if spec.risk_level in {ToolRiskLevel.HIGH_WRITE, ToolRiskLevel.CRITICAL}:
            if not spec.requires_confirmation:
                raise ValueError("high-risk tools must require explicit confirmation")
        self._registry[spec.name] = spec

    def describe(self, name: str) -> ToolSpec:
        try:
            return self._registry[name]
        except KeyError as exc:
            raise ToolExecutionError(f"unknown tool: {name}") from exc

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        spec = self.describe(name)
        context = _TOOL_CONTEXT.get()
        if context is None:
            raise ToolExecutionError("tool execution context is not bound")
        call_id = uuid4().hex
        arguments_hash = _arguments_hash(arguments)
        started = perf_counter()
        retry_count = 0
        error: Exception | None = None
        data: Any = None
        policy_decision = "allowed"
        try:
            self._authorize(spec, context)
            validated = spec.input_model.model_validate(arguments)
            for attempt in range(spec.max_retries + 1):
                try:
                    data = await asyncio.wait_for(
                        spec.handler(validated, context),
                        timeout=spec.timeout_seconds,
                    )
                    error = None
                    break
                except Exception as exc:
                    error = exc
                    if attempt >= spec.max_retries:
                        break
                    retry_count += 1
        except ToolPolicyError as exc:
            error = exc
            policy_decision = "denied"
        except ValidationError as exc:
            error = exc

        elapsed_ms = round((perf_counter() - started) * 1000, 2)
        trace = ToolCallTrace(
            call_id=call_id,
            tool_name=name,
            display_name=spec.display_name or name,
            risk_level=spec.risk_level.value,
            arguments_hash=arguments_hash,
            elapsed_ms=elapsed_ms,
            success=error is None,
            retry_count=retry_count,
            error_type=type(error).__name__ if error is not None else None,
            policy_decision=policy_decision,
        )
        traces = _TOOL_TRACES.get()
        if traces is not None:
            traces.append(trace)
        log_event(
            LOGGER,
            "tool_completed",
            trace_id=context.trace_id or None,
            call_id=call_id,
            tool_name=name,
            risk_level=spec.risk_level.value,
            arguments_hash=arguments_hash,
            elapsed_ms=elapsed_ms,
            success=error is None,
            retry_count=retry_count,
            error_type=trace.error_type,
            policy_decision=policy_decision,
        )
        if error is not None:
            raise ToolExecutionError(f"{name} failed: {type(error).__name__}") from error
        return ToolExecutionResult(
            success=True,
            data=data,
            retry_count=retry_count,
            elapsed_ms=elapsed_ms,
        )

    def _authorize(self, spec: ToolSpec, context: ToolContext) -> None:
        self._sandbox.authorize(
            ToolSandboxRequest(
                tool_name=spec.name,
                agent_role=context.agent_role,
                allowed_roles=frozenset(spec.allowed_roles),
                required_permission=spec.required_permission,
                permissions=frozenset(context.permissions),
                requires_confirmation=spec.requires_confirmation,
                confirmed_actions=frozenset(context.confirmed_actions),
            )
        )


def bind_tool_execution(
    context: ToolContext,
) -> tuple[Token[ToolContext | None], Token[list[ToolCallTrace] | None]]:
    return _TOOL_CONTEXT.set(context), _TOOL_TRACES.set([])


def current_tool_traces() -> list[ToolCallTrace]:
    return list(_TOOL_TRACES.get() or [])


def reset_tool_execution(
    tokens: tuple[Token[ToolContext | None], Token[list[ToolCallTrace] | None]],
) -> None:
    context_token, traces_token = tokens
    _TOOL_CONTEXT.reset(context_token)
    _TOOL_TRACES.reset(traces_token)


def _arguments_hash(arguments: dict[str, Any]) -> str:
    serialized = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
