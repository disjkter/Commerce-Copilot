import io
import json
import logging

from commerce_copilot.observability import (
    JsonFormatter,
    bind_trace_id,
    log_event,
    reset_trace_id,
)
from commerce_copilot.contracts import (
    AgentReport,
    AgentRole,
    GroundingSummary,
    LLMCallTrace,
    ToolCallTrace,
    TurnResponse,
    TurnTrace,
)
from commerce_copilot.metrics import (
    record_turn_failure,
    record_turn_response,
    record_rate_limit,
    record_memory_compaction,
    render_metrics,
    reset_metrics,
)


def test_json_log_contains_trace_and_structured_fields() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("commerce_copilot.test_observability")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    token = bind_trace_id("trace-123")
    try:
        log_event(
            logger,
            "agent_completed",
            role="product_scout",
            elapsed_ms=12.5,
            status="completed",
        )
    finally:
        reset_trace_id(token)
        logger.handlers.clear()

    payload = json.loads(stream.getvalue())
    assert payload["event"] == "agent_completed"
    assert payload["trace_id"] == "trace-123"
    assert payload["role"] == "product_scout"
    assert payload["elapsed_ms"] == 12.5
    assert payload["status"] == "completed"
    assert payload["level"] == "info"
    assert "timestamp" in payload


def test_workflow_metrics_aggregate_agents_tools_llm_and_grounding() -> None:
    reset_metrics()
    response = TurnResponse(
        conversation_id="conversation-1",
        answer="订单正在运输中。",
        elapsed_ms=125,
        reports=[
            AgentReport(
                role=AgentRole.LOGISTICS_TRACKER,
                message="订单正在运输中。",
                elapsed_ms=40,
                tool_calls=[
                    ToolCallTrace(
                        call_id="call-1",
                        tool_name="logistics.get_by_order",
                        display_name="查询订单物流",
                        risk_level="read",
                        arguments_hash="hash",
                        elapsed_ms=12,
                        success=True,
                        retry_count=1,
                    )
                ],
            )
        ],
        trace=TurnTrace(
            trace_id="trace-1",
            planning_source="llm",
            answer_source="local",
            planner_llm=LLMCallTrace(
                provider="deepseek",
                model="deepseek-chat",
                elapsed_ms=30,
                input_tokens=50,
                output_tokens=10,
            ),
            selected_agents=[AgentRole.LOGISTICS_TRACKER],
            grounding=GroundingSummary(passed=True, checked_claims=3),
        ),
    )

    record_turn_response(response)
    metrics = render_metrics()

    assert 'commerce_turns_total{outcome="success"} 1' in metrics
    assert (
        'commerce_agent_executions_total{role="logistics_tracker",status="completed"} 1'
        in metrics
    )
    assert (
        'commerce_tool_calls_total{tool="logistics.get_by_order",risk="read",outcome="success"} 1'
        in metrics
    )
    assert 'commerce_tool_retries_total{tool="logistics.get_by_order"} 1' in metrics
    assert (
        'commerce_llm_calls_total{stage="planner",provider="deepseek",model="deepseek-chat"} 1'
        in metrics
    )
    assert 'commerce_grounding_validations_total{outcome="passed"} 1' in metrics
    assert "commerce_turn_duration_seconds_bucket" in metrics


def test_failed_workflow_is_counted_without_request_identifiers() -> None:
    reset_metrics()

    record_turn_failure("TimeoutError", 250)
    metrics = render_metrics()

    assert 'commerce_turns_total{outcome="failure"} 1' in metrics
    assert 'commerce_business_outcomes_total{outcome="error_TimeoutError"} 1' in metrics
    assert "trace_id" not in metrics
    assert "user_id" not in metrics


def test_rate_limit_metrics_do_not_expose_identity() -> None:
    reset_metrics()

    record_rate_limit("login", allowed=False)
    metrics = render_metrics()

    assert (
        'commerce_rate_limit_checks_total{scope="login",outcome="rejected"} 1'
        in metrics
    )
    assert "testclient" not in metrics


def test_memory_compaction_metrics_count_compacted_turns() -> None:
    reset_metrics()

    record_memory_compaction(3)
    metrics = render_metrics()

    assert 'commerce_memory_compactions_total{outcome="updated"} 1' in metrics
    assert "commerce_memory_turns_compacted_total 3" in metrics

    record_memory_compaction(0, failed=True)
    metrics = render_metrics()
    assert 'commerce_memory_compactions_total{outcome="failed"} 1' in metrics
