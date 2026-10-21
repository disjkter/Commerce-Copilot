import io
import json
import logging

from commerce_copilot.observability import (
    JsonFormatter,
    bind_trace_id,
    log_event,
    reset_trace_id,
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
