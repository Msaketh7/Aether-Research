"""Structured JSON logging.

One line per event, machine-parseable, with the request id attached so a
user-reported failure can be found without guessing. Two rules are enforced
here rather than left to discipline:

* known secret-bearing keys are redacted before serialisation;
* the request id travels in a ``ContextVar``, so a log call deep in a service
  layer does not need it threaded through its signature.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_var: ContextVar[str | None] = ContextVar("user_id", default=None)

# Substrings that mark a value as never-loggable. Matching is on the key name,
# because the values themselves are exactly what must not be inspected.
_REDACTED_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
)

_REDACTED = "[redacted]"

# Attributes LogRecord always carries; anything else was added by the caller
# and belongs in the structured payload.
_STANDARD_ATTRS = frozenset(
    [
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    ]
)


#: The names ``logging.makeRecord`` refuses to let an ``extra`` overwrite.
#: Derived from a real record rather than listed, so a future Python that adds
#: an attribute cannot reintroduce the collision. It overlaps
#: ``_STANDARD_ATTRS`` above, which answers a different question: that one is
#: what the formatter treats as *not* caller-supplied.
_RESERVED_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
}


def log_context(values: Mapping[str, Any]) -> dict[str, Any]:
    """Caller-supplied fields, made safe to pass as ``extra``.

    ``logging.makeRecord`` raises ``KeyError`` when an extra field shares a name
    with a ``LogRecord`` attribute. That turns a log call into an exception, and
    where the log call is inside an error handler it turns a 415 into a 500 -
    which is exactly how this was found (Phase 10): an upload refused for a
    format mismatch carried ``filename`` in its error context.

    Colliding keys are prefixed rather than dropped. The value is what an
    operator needs; the name is only how they find it.
    """
    return {
        (f"ctx_{key}" if key in _RESERVED_ATTRS else key): value for key, value in values.items()
    }


def redact(key: str, value: Any) -> Any:
    """Replace a value whose key marks it as sensitive."""
    lowered = key.lower()
    if any(part in lowered for part in _REDACTED_KEY_PARTS):
        return _REDACTED
    if isinstance(value, dict):
        return {k: redact(k, v) for k, v in value.items()}
    return value


class JsonFormatter(logging.Formatter):
    """Renders a ``LogRecord`` as a single JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }

        if (request_id := request_id_var.get()) is not None:
            payload["request_id"] = request_id
        if (user_id := user_id_var.get()) is not None:
            payload["user_id"] = user_id

        for key, value in record.__dict__.items():
            if key in _STANDARD_ATTRS or key.startswith("_"):
                continue
            payload[key] = redact(key, value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(level: str = "info") -> None:
    """Install the JSON formatter on the root logger.

    Idempotent: calling it twice (app startup plus a test fixture) does not
    duplicate handlers, which would otherwise double every log line.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Uvicorn ships its own handlers; let records propagate to ours instead so
    # access logs and application logs share one format.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # SQLAlchemy's INFO level echoes every statement; that is a debug decision.
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
