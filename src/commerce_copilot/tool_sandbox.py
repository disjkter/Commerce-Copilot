"""Deterministic policy boundary for Agent tool execution."""

from __future__ import annotations

from dataclasses import dataclass


class ToolPolicyError(RuntimeError):
    """Raised when a tool call violates the execution sandbox policy."""


@dataclass(frozen=True, slots=True)
class ToolSandboxRequest:
    tool_name: str
    agent_role: str | None
    allowed_roles: frozenset[str]
    required_permission: str | None
    permissions: frozenset[str]
    requires_confirmation: bool
    confirmed_actions: frozenset[str]


class ToolSandbox:
    """Fail-closed, application-level sandbox for registered business tools.

    The sandbox does not execute tools and does not trust model output. It only
    decides whether the application-created execution context grants a call.
    """

    def authorize(self, request: ToolSandboxRequest) -> None:
        if request.allowed_roles and request.agent_role not in request.allowed_roles:
            raise ToolPolicyError(
                f"agent role {request.agent_role or 'unknown'} cannot call "
                f"{request.tool_name}"
            )
        if (
            request.required_permission
            and request.required_permission not in request.permissions
        ):
            raise ToolPolicyError(
                f"missing permission {request.required_permission} for "
                f"{request.tool_name}"
            )
        if (
            request.requires_confirmation
            and request.tool_name not in request.confirmed_actions
        ):
            raise ToolPolicyError(
                f"explicit confirmation required for {request.tool_name}"
            )
