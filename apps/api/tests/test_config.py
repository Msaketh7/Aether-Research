"""Configuration parsing.

The regression these tests exist for: pydantic-settings JSON-decodes complex
fields *before* a `mode="before"` validator sees them, so the comma-separated
form documented in `.env.example` made the process fail to start. Config that
cannot load the file the repository ships is a defect, and it is only ever
caught by running the thing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models import build_providers


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://localhost:3000", ["http://localhost:3000"]),
        ("http://a.test, http://b.test", ["http://a.test", "http://b.test"]),
        ('["http://a.test","http://b.test"]', ["http://a.test", "http://b.test"]),
        ("", []),
    ],
)
def test_cors_origins_accept_both_documented_forms(monkeypatch, raw, expected):
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", raw)
    assert Settings(app_env="test").cors_allow_origins == expected


def load_env_example(monkeypatch) -> None:
    """Put `.env.example` into the environment, as `make env` effectively does."""
    example = Path(__file__).resolve().parents[3] / ".env.example"
    for line in example.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        monkeypatch.setenv(key.strip(), value.strip().strip('"'))


def test_the_shipped_env_example_parses(monkeypatch):
    """Every non-secret value in .env.example must load."""
    load_env_example(monkeypatch)

    settings = Settings()

    assert settings.app_env == "local"
    assert settings.cors_allow_origins == ["http://localhost:3000"]
    assert settings.max_research_iterations == 4
    assert settings.max_estimated_cost_usd == 2.00


def test_a_process_started_from_the_shipped_example_builds_its_providers(monkeypatch):
    """`make env` copies .env.example, where every key is present and blank. Read
    as a value, a blank key is a `SecretStr("")` rather than an absent one - so a
    provider was built with it and the vendor SDK refused it in its constructor,
    crashing the process at startup. Found by starting a worker that way."""
    load_env_example(monkeypatch)

    settings = Settings()

    assert settings.openai_api_key is None
    assert settings.anthropic_api_key is None
    assert settings.tavily_api_key is None
    # Ollama needs no credential, which is what a developer with nothing
    # configured is left with - and is the honest answer, not a crash.
    assert sorted(provider.value for provider in build_providers(settings)) == ["ollama"]


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_credential_set_to_nothing_is_absent_rather_than_empty(monkeypatch, blank):
    monkeypatch.setenv("OPENAI_API_KEY", blank)
    monkeypatch.setenv("GITHUB_TOKEN", blank)

    settings = Settings(app_env="test")

    assert settings.openai_api_key is None
    assert settings.github_token is None


def test_production_hides_the_interactive_docs():
    """Interactive docs are a development convenience, not a public surface."""
    assert Settings(app_env="production").docs_url is None
    assert Settings(app_env="local").docs_url == "/docs"


def test_run_ceilings_have_defaults_matching_fr8():
    settings = Settings(app_env="test")

    assert settings.max_research_iterations == 4
    assert settings.max_sources == 50
    assert settings.max_runtime_seconds == 300
    assert settings.max_estimated_cost_usd == 2.00


# --- the worker (Phase 13) ------------------------------------------------


def test_a_lease_shorter_than_a_node_is_refused():
    """A worker renews its lease at node boundaries, so a lease that expires
    while a node is still running lets a second worker take a run that is
    perfectly healthy - and then both hold it. Refused at startup, because the
    symptom is a duplicated research run hours later."""
    with pytest.raises(ValidationError, match="WORKER_LEASE_SECONDS"):
        Settings(app_env="test", graph_node_timeout_seconds=120.0, worker_lease_seconds=100)

    assert Settings(app_env="test", graph_node_timeout_seconds=120.0, worker_lease_seconds=240)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("worker_concurrency", 0),
        ("worker_max_attempts", 0),
        ("worker_sweep_batch", 0),
        ("worker_poll_seconds", 0.0),
        ("worker_shutdown_grace_seconds", -1.0),
    ],
)
def test_a_worker_bound_that_could_not_bound_anything_is_refused(field, value):
    with pytest.raises(ValidationError, match=field.upper()):
        Settings(app_env="test", **{field: value})


def test_a_backoff_ceiling_below_its_floor_is_refused():
    with pytest.raises(ValidationError, match="WORKER_RETRY_MAX_DELAY_SECONDS"):
        Settings(
            app_env="test",
            worker_retry_base_delay_seconds=60.0,
            worker_retry_max_delay_seconds=10.0,
        )
