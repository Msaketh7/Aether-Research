"""The error contract and the logging guarantees.

Both are cross-cutting promises rather than features, which is exactly why they
need tests: nothing else fails visibly when they break.
"""

from __future__ import annotations

import json
import logging

import pytest
from httpx import AsyncClient

from app.core.logging import JsonFormatter, redact, request_id_var
from tests.conftest import API

# --- error envelope -------------------------------------------------------


async def test_an_unknown_route_uses_the_project_envelope(client: AsyncClient):
    """Framework 404s must not leak Starlette's `{"detail": ...}` shape."""
    response = await client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    body = response.json()
    assert "error" in body
    assert body["error"]["code"] == "http_404"
    assert "detail" not in body


async def test_a_wrong_method_uses_the_project_envelope(client: AsyncClient):
    response = await client.delete(f"{API}/research")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "http_405"


async def test_every_error_carries_the_request_id(client: AsyncClient):
    """A user can report `trace_id` and it can be found in the logs."""
    response = await client.get(f"{API}/research/00000000-0000-4000-8000-00000000ffff")

    assert response.json()["error"]["trace_id"] == response.headers["x-request-id"]


async def test_validation_details_are_keyed_by_the_field_the_client_sent(client: AsyncClient):
    """The frontend renders each message next to the input that caused it."""
    response = await client.post(
        f"{API}/research", json={"question": "short", "mode": "not-a-mode"}
    )

    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert set(details) == {"question", "mode"}
    # Pydantic's "Value error, " prefix is an implementation detail, not a message.
    assert not any(m.startswith("Value error") for ms in details.values() for m in ms)


async def test_a_not_implemented_capability_says_so(client: AsyncClient):
    """501 rather than an empty 200: 'not built yet' must be distinguishable.

    Asserted against a capability that genuinely has not landed. This used to
    point at `/evaluations`, which Phase 18 implemented - and the test failing
    was the correct signal that it had.
    """
    response = await client.get(f"{API}/settings")

    assert response.status_code == 501
    assert response.json()["error"]["code"] == "settings_not_implemented"


async def test_unexpected_exceptions_do_not_leak_internals(
    tolerant_client: AsyncClient, monkeypatch
):
    """The client gets a generic message; the detail goes only to the log."""
    from app.research import service as service_module

    secret = "postgres://user:hunter2@db/aether"  # noqa: S105 - fake, asserted absent

    async def explode(*_: object, **__: object) -> None:
        raise RuntimeError(f"connection string {secret}")

    monkeypatch.setattr(service_module.ResearchService, "stats", explode)

    response = await tolerant_client.get(f"{API}/research/stats")

    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal_error"
    assert secret not in response.text
    assert "RuntimeError" not in response.text
    assert "Traceback" not in response.text


# --- logging --------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    ["password", "api_key", "ANTHROPIC_API_KEY", "authorization", "session_token", "credential"],
)
def test_sensitive_keys_are_redacted(key):
    assert redact(key, "hunter2") == "[redacted]"


def test_redaction_reaches_nested_values():
    result = redact("config", {"host": "db", "password": "hunter2"})
    assert result == {"host": "db", "password": "[redacted]"}


def test_ordinary_keys_are_not_redacted():
    assert redact("run_id", "abc") == "abc"


def test_log_records_serialise_as_one_json_object():
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="research run created",
        args=(),
        exc_info=None,
    )
    record.run_id = "abc-123"
    record.api_key = "sk-should-not-appear"

    token = request_id_var.set("trace-9")
    try:
        payload = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)

    assert payload["message"] == "research run created"
    assert payload["level"] == "info"
    assert payload["run_id"] == "abc-123"
    assert payload["request_id"] == "trace-9"
    assert payload["api_key"] == "[redacted]"
    assert "sk-should-not-appear" not in json.dumps(payload)


def test_secrets_are_not_printed_by_settings_repr():
    """SecretStr means an accidental log of the settings object is harmless."""
    from app.core.config import Settings

    settings = Settings(app_env="test", anthropic_api_key="sk-ant-secret-value")

    assert "sk-ant-secret-value" not in repr(settings)
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "sk-ant-secret-value"


def test_running_migrations_does_not_switch_the_application_logs_off():
    """Found in Phase 10, by a test that could not see a log line it had just made.

    ``fileConfig`` defaults to ``disable_existing_loggers=True``, and
    ``migrations/env.py`` imports the application to reach its metadata - so
    every ``app.*`` logger already exists by the time Alembic configures
    logging, and every one of them was being disabled. The migrations kept
    logging normally, which is what made it invisible: the process looked like
    it was logging, and the application inside it had gone quiet.

    Harmless while migrations run in their own process. Not harmless in a
    worker that migrates and then serves.
    """
    from logging.config import fileConfig
    from pathlib import Path

    probe = logging.getLogger("app.probe.migrations")
    assert not probe.disabled

    fileConfig(
        str(Path(__file__).resolve().parents[1] / "alembic.ini"),
        disable_existing_loggers=False,
    )

    assert not probe.disabled, "an application logger must survive the migration run"


def test_an_error_context_cannot_break_the_handler_that_reports_it():
    """Found in Phase 10, the moment application logs started working again.

    ``logging.makeRecord`` raises when an ``extra`` field shares a name with a
    ``LogRecord`` attribute. The error handler spreads an ``AppError``'s context
    into ``extra``, and an upload refused for a format mismatch carries
    ``filename`` in its context - so reporting a 415 raised a ``KeyError`` and
    turned it into a 500. The disabled loggers above are why no test saw it: a
    disabled logger returns before it builds a record.
    """
    from app.core.logging import log_context

    safe = log_context({"filename": "notes.pdf", "declared": "application/pdf"})

    assert safe == {"ctx_filename": "notes.pdf", "declared": "application/pdf"}
    logging.getLogger("app.probe.context").warning("request rejected", extra=safe)


def test_no_log_call_in_the_application_names_a_reserved_field():
    """The same defect as above, from the other direction, and across the codebase.

    ``log_context`` protects the fields a *caller* supplies. It does nothing for
    the ``extra={...}`` literals written by hand, and three of those named
    ``created`` - which every ``LogRecord`` already has. The suite pins
    ``log_level`` to ``warning``, so those calls never built a record and never
    raised; at the default level they raise every time, and one of them sits in
    the ingestion pipeline where the failure is swallowed per page. A worker
    would then gather nothing and every run would fail at synthesis, with the
    cause several layers away (Phase 19).

    So this reads the source rather than exercising a path: the class of bug is
    "a literal nobody executed at this level", and only a scan finds all of it.
    """
    import ast
    from pathlib import Path

    from app.core.logging import _RESERVED_ATTRS

    root = Path(__file__).resolve().parents[1] / "app"
    offenders: list[str] = []
    for source in root.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            extra = next((kw.value for kw in node.keywords if kw.arg == "extra"), None)
            if not isinstance(extra, ast.Dict):
                continue
            for key in extra.keys:
                if isinstance(key, ast.Constant) and key.value in _RESERVED_ATTRS:
                    offenders.append(
                        f"{source.relative_to(root.parent)}:{key.lineno} names {key.value!r}"
                    )

    assert not offenders, (
        "logging.makeRecord raises KeyError on these, but only when the logger is "
        "enabled at that level: " + "; ".join(offenders)
    )
