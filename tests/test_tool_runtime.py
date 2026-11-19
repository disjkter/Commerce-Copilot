import asyncio

import pytest
from pydantic import BaseModel, Field

from commerce_copilot.contracts import AgentRole
from commerce_copilot.domain_tools import get_tool_runtime
from commerce_copilot.orchestrator import handle_turn
from commerce_copilot.tool_runtime import (
    ToolContext,
    ToolExecutionError,
    ToolRiskLevel,
    ToolRuntime,
    ToolSpec,
    bind_tool_execution,
    current_tool_traces,
    reset_tool_execution,
)


class DemoInput(BaseModel):
    value: int = Field(gt=0)


def test_read_tool_retries_and_records_trace():
    attempts = 0

    async def flaky(arguments: BaseModel, context: ToolContext):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("temporary")
        return {"value": DemoInput.model_validate(arguments).value, "user": context.user_id}

    runtime = ToolRuntime()
    runtime.register(
        ToolSpec(
            name="demo.read",
            display_name="测试查询",
            description="test",
            input_model=DemoInput,
            risk_level=ToolRiskLevel.READ,
            timeout_seconds=1,
            max_retries=1,
            handler=flaky,
        )
    )
    tokens = bind_tool_execution(ToolContext(user_id="user-1", trace_id="trace-1"))
    try:
        result = asyncio.run(runtime.execute("demo.read", {"value": 2}))
        traces = current_tool_traces()
    finally:
        reset_tool_execution(tokens)

    assert result.data == {"value": 2, "user": "user-1"}
    assert traces[0].success is True
    assert traces[0].display_name == "测试查询"
    assert traces[0].retry_count == 1
    assert len(traces[0].arguments_hash) == 16


def test_invalid_arguments_are_blocked_and_traced():
    async def handler(arguments: BaseModel, context: ToolContext):
        raise AssertionError("handler must not run")

    runtime = ToolRuntime()
    runtime.register(
        ToolSpec(
            name="demo.validated",
            display_name="参数校验测试",
            description="test",
            input_model=DemoInput,
            risk_level=ToolRiskLevel.READ,
            handler=handler,
        )
    )
    tokens = bind_tool_execution(ToolContext(user_id="user-1"))
    try:
        with pytest.raises(ToolExecutionError):
            asyncio.run(runtime.execute("demo.validated", {"value": 0}))
        traces = current_tool_traces()
    finally:
        reset_tool_execution(tokens)

    assert traces[0].success is False
    assert traces[0].error_type == "ValidationError"


def test_write_tools_cannot_enable_blind_retries():
    async def handler(arguments: BaseModel, context: ToolContext):
        return None

    runtime = ToolRuntime()
    with pytest.raises(ValueError, match="write tools"):
        runtime.register(
            ToolSpec(
                name="demo.write",
                display_name="写入测试",
                description="test",
                input_model=DemoInput,
                risk_level=ToolRiskLevel.HIGH_WRITE,
                max_retries=1,
                handler=handler,
            )
        )


def test_order_and_logistics_agents_expose_read_tool_traces():
    order = asyncio.run(
        handle_turn(
            "查询订单 ORD-2026-1001 的状态",
            user_id="user-demo",
            local_only=True,
        )
    )
    logistics = asyncio.run(
        handle_turn(
            "订单 ORD-2026-1001 的物流到哪了",
            user_id="user-demo",
            local_only=True,
        )
    )

    assert order.reports[0].role == AgentRole.ORDER_CARE
    assert order.reports[0].tool_calls[0].tool_name == "order.get"
    assert order.reports[0].tool_calls[0].risk_level == "read"
    assert logistics.reports[0].role == AgentRole.LOGISTICS_TRACKER
    assert logistics.reports[0].tool_calls[0].tool_name == "logistics.get_by_order"
    assert all(item.success for item in order.reports[0].tool_calls)
    assert all(item.success for item in logistics.reports[0].tool_calls)


def test_domain_tool_registry_contains_initial_read_tools():
    runtime = get_tool_runtime()

    assert runtime.describe("order.get").risk_level == ToolRiskLevel.READ
    assert runtime.describe("logistics.get_by_tracking").max_retries == 1
    assert runtime.describe("returns.create_request").requires_confirmation is True
    assert runtime.describe("returns.cancel_request").required_permission == "returns:write"


def test_high_risk_tool_is_blocked_without_policy_grants_and_traced():
    called = False

    async def handler(arguments: BaseModel, context: ToolContext):
        nonlocal called
        called = True
        return {"ok": True}

    runtime = ToolRuntime()
    runtime.register(
        ToolSpec(
            name="demo.high_write",
            description="test policy",
            input_model=DemoInput,
            risk_level=ToolRiskLevel.HIGH_WRITE,
            allowed_roles={"writer"},
            required_permission="demo:write",
            requires_confirmation=True,
            handler=handler,
        )
    )
    tokens = bind_tool_execution(
        ToolContext(
            user_id="user-1",
            agent_role="reader",
            permissions={"demo:write"},
            confirmed_actions={"demo.high_write"},
        )
    )
    try:
        with pytest.raises(ToolExecutionError, match="failed"):
            asyncio.run(runtime.execute("demo.high_write", {"value": 1}))
        traces = current_tool_traces()
    finally:
        reset_tool_execution(tokens)

    assert called is False
    assert traces[0].policy_decision == "denied"
    assert traces[0].error_type == "ToolPolicyError"


def test_high_risk_tool_runs_only_with_role_permission_and_confirmation():
    async def handler(arguments: BaseModel, context: ToolContext):
        return {"user_id": context.user_id}

    runtime = ToolRuntime()
    runtime.register(
        ToolSpec(
            name="demo.high_write",
            description="test policy",
            input_model=DemoInput,
            risk_level=ToolRiskLevel.HIGH_WRITE,
            allowed_roles={"writer"},
            required_permission="demo:write",
            requires_confirmation=True,
            handler=handler,
        )
    )
    tokens = bind_tool_execution(
        ToolContext(
            user_id="user-1",
            agent_role="writer",
            permissions={"demo:write"},
            confirmed_actions={"demo.high_write"},
        )
    )
    try:
        result = asyncio.run(runtime.execute("demo.high_write", {"value": 1}))
        traces = current_tool_traces()
    finally:
        reset_tool_execution(tokens)

    assert result.data == {"user_id": "user-1"}
    assert traces[0].policy_decision == "allowed"


def test_high_risk_tool_rejects_missing_confirmation():
    async def handler(arguments: BaseModel, context: ToolContext):
        raise AssertionError("unconfirmed handler must not run")

    runtime = ToolRuntime()
    runtime.register(
        ToolSpec(
            name="demo.high_write",
            description="test confirmation",
            input_model=DemoInput,
            risk_level=ToolRiskLevel.HIGH_WRITE,
            allowed_roles={"writer"},
            required_permission="demo:write",
            requires_confirmation=True,
            handler=handler,
        )
    )
    tokens = bind_tool_execution(
        ToolContext(
            user_id="user-1",
            agent_role="writer",
            permissions={"demo:write"},
        )
    )
    try:
        with pytest.raises(ToolExecutionError):
            asyncio.run(runtime.execute("demo.high_write", {"value": 1}))
        traces = current_tool_traces()
    finally:
        reset_tool_execution(tokens)

    assert traces[0].policy_decision == "denied"
    assert traces[0].error_type == "ToolPolicyError"
