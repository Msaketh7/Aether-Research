"""Typed configuration.

Every environment variable the service reads is declared here, exactly once.
Nothing in the codebase calls ``os.environ`` at a use site: a setting that is not
in this file does not exist, which is what makes the configuration surface
auditable and the threat model's "secrets are read through a typed layer" claim
true rather than aspirational.

Secrets are ``SecretStr`` so that an accidental log or repr prints
``**********`` instead of the value.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# apps/api/app/core/config.py -> repository root
REPO_ROOT = Path(__file__).resolve().parents[4]

Environment = Literal["local", "test", "staging", "production"]


class Settings(BaseSettings):
    """Runtime configuration, loaded from the environment and the root ``.env``."""

    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", REPO_ROOT / "apps" / "api" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- application ------------------------------------------------------
    app_env: Environment = "local"
    log_level: Literal["debug", "info", "warning", "error"] = "info"
    api_host: str = "0.0.0.0"  # noqa: S104 - binding all interfaces is intended in a container
    api_port: int = 8000

    # NoDecode keeps pydantic-settings from JSON-decoding the raw value, so the
    # comma-separated form documented in .env.example reaches the validator
    # below. Without it, `CORS_ALLOW_ORIGINS=http://localhost:3000` fails to
    # parse and the process refuses to start.
    cors_allow_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # --- datastores -------------------------------------------------------
    database_url: str = "postgresql+asyncpg://aether:aether@localhost:5432/aether"
    redis_url: str = "redis://localhost:6379/0"

    # Pool sizing. Deliberately explicit: an unbounded pool turns a slow query
    # into a connection exhaustion incident (TDD 7.3).
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_pool_timeout_seconds: int = 10
    db_command_timeout_seconds: int = 30

    # --- run governance (FR-8, enforced from Phase 9) ---------------------
    max_research_iterations: int = 4
    max_sources: int = 50
    max_search_queries: int = 30
    max_runtime_seconds: int = 300
    max_estimated_cost_usd: float = 2.00

    # Per-user guardrails on the API surface itself.
    max_concurrent_runs_per_user: int = 3
    default_page_size: int = 20
    max_page_size: int = 100

    # --- SSE --------------------------------------------------------------
    sse_heartbeat_seconds: int = 15
    # A stream that outlives this is closed; the browser reconnects with
    # Last-Event-ID, which is cheaper than holding a connection open forever.
    sse_max_connection_seconds: int = 900
    sse_replay_buffer_size: int = 500

    # --- providers (used from Phase 5; declared so the surface is complete)
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    tavily_api_key: SecretStr | None = None
    github_token: SecretStr | None = None

    @field_validator("cors_allow_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string, which is how .env carries a list.

        A JSON array is accepted too, so both `a,b` and `["a","b"]` work.
        """
        if isinstance(value, str):
            raw = value.strip()
            if raw.startswith("["):
                parsed = json.loads(raw)
                return [str(origin).strip() for origin in parsed]
            return [origin.strip() for origin in raw.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def docs_url(self) -> str | None:
        """Interactive docs are a development convenience, not a public surface."""
        return None if self.is_production else "/docs"


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton.

    Cached so that a request never pays for environment parsing, and so tests
    can clear the cache to swap configuration deterministically.
    """
    return Settings()
