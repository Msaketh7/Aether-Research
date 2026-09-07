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

    # --- object storage (ADR 0010) ----------------------------------------
    # `None` resolves to the filesystem backend under APP_ENV=test and to S3
    # everywhere else, so a real deployment cannot fall back to local disk by
    # omission. See `app.storage.build_object_storage`.
    storage_backend: Literal["s3", "filesystem"] | None = None
    storage_local_path: Path = REPO_ROOT / ".data" / "object-storage"

    # Unset endpoint means real AWS S3; set it to a MinIO URL locally.
    s3_endpoint_url: str | None = None
    s3_bucket: str = "aether-artifacts"
    s3_region: str = "us-east-1"
    # Unset credentials are intended: botocore's default chain then resolves
    # them, which on ECS is the task role, so production stores no S3 keys.
    s3_access_key_id: SecretStr | None = None
    s3_secret_access_key: SecretStr | None = None
    s3_addressing_style: Literal["auto", "path", "virtual"] = "auto"

    # Every external call is bounded (project rule). A hung S3 read inside a
    # worker is a research run that stalls without ever explaining why.
    s3_connect_timeout_seconds: int = 5
    s3_read_timeout_seconds: int = 30
    s3_max_attempts: int = 3

    #: Ceiling on a single artifact, enforced on read as well as write. 25 MiB
    #: comfortably holds an SEC filing or a long paper; anything larger is a
    #: decompression bomb or a bug, and either way must not be buffered.
    max_artifact_bytes: int = 25 * 1024 * 1024

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

    # --- model gateway (ADR 0007) -----------------------------------------
    # A provider with no credential is simply not built. Ollama needs none,
    # which is what keeps local development free of API keys.
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    ollama_base_url: str = "http://localhost:11434"

    #: Declared models live in YAML so repricing is a reviewable diff. Absolute
    #: path, or None for the file shipped beside `app/models/`.
    model_registry_path: Path | None = None

    # Bounds on the gateway. A model call is the slowest and most expensive
    # thing the system does, so none of these has an unbounded default.
    llm_request_timeout_seconds: float = 60.0
    llm_max_attempts: int = 3
    llm_retry_base_delay_seconds: float = 0.5
    llm_retry_max_delay_seconds: float = 8.0
    #: Fifty parallel researchers become this many concurrent calls and the
    #: rest queue (TDD 6.3). Without it, fan-out becomes a rate-limit wall.
    llm_max_concurrent_calls: int = 8

    # --- research tools (Phase 6) -----------------------------------------
    search_provider: Literal["tavily", "brave"] = "tavily"
    tavily_api_key: SecretStr | None = None
    brave_api_key: SecretStr | None = None
    github_token: SecretStr | None = None

    #: SEC's access terms require a descriptive User-Agent with a contact
    #: address; anonymous scrapers get blocked. Also sent on ordinary fetches,
    #: because being identifiable is how a crawler keeps its access.
    sec_user_agent: str = "AetherResearch/0.1 (contact@example.com)"

    # Bounds on every outbound fetch. None of these is optional: an unbounded
    # fetch is a research run that hangs on a slow server, and an unbounded
    # response is one hostile page away from an out-of-memory kill.
    fetch_connect_timeout_seconds: float = 5.0
    fetch_read_timeout_seconds: float = 20.0
    fetch_max_response_bytes: int = 5 * 1024 * 1024
    fetch_max_redirects: int = 5

    tool_timeout_seconds: float = 30.0
    tool_max_attempts: int = 3
    tool_max_concurrent_calls: int = 8

    #: Per-deployment SSRF policy (TDD 15.2). `allowed_domains` empty means "any
    #: public host"; setting it turns the fetcher into an allowlist-only client,
    #: which is what a locked-down deployment wants.
    allowed_domains: Annotated[list[str], NoDecode] = Field(default_factory=list)
    blocked_domains: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("allowed_domains", "blocked_domains", mode="before")
    @classmethod
    def _split_domains(cls, value: object) -> object:
        """Same comma-separated form as CORS origins, for the same reason."""
        return cls._split_origins(value)

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
    def resolved_storage_backend(self) -> Literal["s3", "filesystem"]:
        """The backend to build, with the test-environment default applied.

        Explicit configuration always wins, so a developer can point a local
        test run at MinIO. Only the absence of a choice is resolved by
        environment - and it resolves to S3 outside tests, never the reverse.
        """
        if self.storage_backend is not None:
            return self.storage_backend
        return "filesystem" if self.app_env == "test" else "s3"

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
