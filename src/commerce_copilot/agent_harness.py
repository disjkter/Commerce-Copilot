"""Unified lifecycle harness for domain Agent workers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from time import perf_counter

from .contracts import AgentReport, AgentRole, Claim, Evidence, Mission
from .observability import log_event
from .tool_runtime import (
    ToolContext,
    bind_tool_execution,
    current_tool_traces,
    reset_tool_execution,
)


LOGGER = logging.getLogger("commerce_copilot.agent_harness")
AgentDraft = tuple[str, list[Evidence], list[Claim]]
AgentHandler = Callable[[Mission], Awaitable[AgentDraft]]
ConfirmationResolver = Callable[[Mission], set[str]]


@dataclass(frozen=True, slots=True)
class AgentHarnessPolicy:
    timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("agent timeout must be positive")


class AgentRuntimeHarness:
    """Run every Worker through the same context, safety and trace contract."""

    def __init__(
        self,
        *,
        handlers: Mapping[AgentRole, AgentHandler],
        role_permissions: Mapping[AgentRole, set[str]],
        confirmation_resolver: ConfirmationResolver,
        policy: AgentHarnessPolicy | None = None,
    ) -> None:
        self._handlers = dict(handlers)
        self._role_permissions = {
            role: frozenset(permissions)
            for role, permissions in role_permissions.items()
        }
        self._confirmation_resolver = confirmation_resolver
        self._policy = policy or AgentHarnessPolicy()
        missing_roles = set(self._handlers) - set(self._role_permissions)
        if missing_roles:
            names = ", ".join(sorted(role.value for role in missing_roles))
            raise ValueError(f"missing tool permissions for Agent roles: {names}")

    async def execute(self, mission: Mission, *, trace_id: str = "") -> AgentReport:
        started = perf_counter()
        handler = self._handlers.get(mission.role)
        if handler is None:
            return self._failed_report(
                mission,
                started,
                error_type="UnknownAgentRole",
            )

        configured_user_id = mission.parameters.get("user_id")
        context = ToolContext(
            user_id=(
                str(configured_user_id) if configured_user_id else "anonymous"
            ),
            trace_id=trace_id,
            conversation_id=(
                str(mission.parameters["conversation_id"])
                if mission.parameters.get("conversation_id")
                else None
            ),
            agent_role=mission.role.value,
            permissions=set(self._role_permissions[mission.role]),
            confirmed_actions=self._confirmation_resolver(mission),
        )
        tokens = bind_tool_execution(context)
        log_event(
            LOGGER,
            "agent_harness_started",
            trace_id=trace_id or None,
            agent_role=mission.role.value,
        )
        try:
            try:
                message, evidence, claims = await asyncio.wait_for(
                    handler(mission),
                    timeout=self._policy.timeout_seconds,
                )
                status = "completed"
                error_type = None
            except Exception as exc:  # One branch must not erase sibling results.
                message = f"{mission.role.value} 暂时不可用：{type(exc).__name__}"
                evidence = []
                claims = []
                status = "failed"
                error_type = type(exc).__name__
            tool_calls = current_tool_traces()
        finally:
            reset_tool_execution(tokens)

        elapsed_ms = round((perf_counter() - started) * 1000, 2)
        log_event(
            LOGGER,
            "agent_harness_completed",
            trace_id=trace_id or None,
            agent_role=mission.role.value,
            status=status,
            error_type=error_type,
            tool_call_count=len(tool_calls),
            evidence_count=len(evidence),
            elapsed_ms=elapsed_ms,
        )
        return AgentReport(
            role=mission.role,
            message=message,
            evidence=evidence,
            claims=claims,
            tool_calls=tool_calls,
            elapsed_ms=elapsed_ms,
            status=status,
        )

    @staticmethod
    def _failed_report(
        mission: Mission,
        started: float,
        *,
        error_type: str,
    ) -> AgentReport:
        return AgentReport(
            role=mission.role,
            message=f"{mission.role.value} 暂时不可用：{error_type}",
            elapsed_ms=round((perf_counter() - started) * 1000, 2),
            status="failed",
        )
