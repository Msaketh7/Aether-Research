"""Configuration parsing.

The regression these tests exist for: pydantic-settings JSON-decodes complex
fields *before* a `mode="before"` validator sees them, so the comma-separated
form documented in `.env.example` made the process fail to start. Config that
cannot load the file the repository ships is a defect, and it is only ever
caught by running the thing.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings


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


def test_the_shipped_env_example_parses(monkeypatch):
    """Every non-secret value in .env.example must load."""
    from pathlib import Path

    example = Path(__file__).resolve().parents[3] / ".env.example"
    for line in example.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        monkeypatch.setenv(key.strip(), value.strip().strip('"'))

    settings = Settings()

    assert settings.app_env == "local"
    assert settings.cors_allow_origins == ["http://localhost:3000"]
    assert settings.max_research_iterations == 4
    assert settings.max_estimated_cost_usd == 2.00


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
