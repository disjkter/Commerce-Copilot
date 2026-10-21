import json
import logging
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any


_TRACE_ID: ContextVar[str | None] = ContextVar("commerce_trace_id", default=None)
_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
        }
        trace_id = getattr(record, "trace_id", None) or _TRACE_ID.get()
        if trace_id:
            payload["trace_id"] = trace_id
        details = getattr(record, "details", None)
        if isinstance(details, dict):
            payload.update(details)
        if record.exc_info:
            payload["exception"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("commerce_copilot")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(level.upper())
    logger.propagate = False
    _CONFIGURED = True


def bind_trace_id(trace_id: str) -> Token[str | None]:
    return _TRACE_ID.set(trace_id)


def reset_trace_id(token: Token[str | None]) -> None:
    _TRACE_ID.reset(token)


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    trace_id: str | None = None,
    exc_info: bool = False,
    **details: object,
) -> None:
    logger.log(
        level,
        event,
        extra={"event": event, "trace_id": trace_id, "details": details},
        exc_info=exc_info,
    )
