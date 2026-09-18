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
import os
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
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

    # --- research graph (Phase 9, ADR 0014) -------------------------------
    #: Subtasks one planning round may dispatch. A plan may propose up to 20;
    #: the rest wait for a later round, highest priority first.
    max_subtasks_per_iteration: int = 8
    #: Researchers that run at once. The gateway already caps concurrent model
    #: calls; this also caps a run's concurrent fetches and database sessions.
    graph_max_concurrency: int = 4
    #: The most any one node - a single researcher included - may run. The
    #: run's runtime ceiling is checked between nodes; this is what bounds a
    #: node that never returns.
    graph_node_timeout_seconds: float = 120.0
    #: Connections the checkpointer may hold. psycopg rather than asyncpg,
    #: because LangGraph's Postgres checkpointer is written against it.
    checkpoint_pool_size: int = 4

    # --- worker (Phase 13, ADR 0005) --------------------------------------
    #: Runs one worker process executes at once. A run is mostly waiting on the
    #: network, so a process can hold several; the ceiling is the database pool
    #: and the gateway's semaphore, which are per process and shared by all of
    #: them. Scale past this with more processes, not a bigger number.
    worker_concurrency: int = 1
    #: How long the worker blocks waiting for a job before ticking. The tick is
    #: what runs the reconciliation sweep and notices a shutdown signal, so this
    #: is also the worst case for how long a stopping worker takes to react.
    worker_poll_seconds: float = 5.0
    #: Attempts a run gets before it is marked failed. An attempt is counted
    #: when a worker claims the run, so a process killed mid-run spends one -
    #: which is what stops a run that crashes its worker from doing it forever.
    worker_max_attempts: int = 3
    #: Exponential backoff between attempts, held in the run's own row rather
    #: than in a delayed queue: a retry that Redis forgets is a run that never
    #: resumes, and the sweep already reads Postgres.
    worker_retry_base_delay_seconds: float = 10.0
    worker_retry_max_delay_seconds: float = 600.0
    #: How long a worker's claim on a run is honoured without a heartbeat.
    #: The heartbeat is written at every node boundary, so this must comfortably
    #: exceed one node's own timeout or a healthy worker's run gets taken from
    #: it mid-node; the validator below enforces that.
    worker_lease_seconds: int = 600
    #: How often a worker re-dispatches runs the queue lost, retries that are
    #: due, and runs whose worker died (ADR 0005).
    worker_sweep_interval_seconds: float = 30.0
    #: How long a run may sit `queued` before the sweep concludes its job was
    #: lost. Below this, a newly created run would be dispatched twice - which
    #: the claim makes harmless, but noisy.
    worker_queued_grace_seconds: float = 60.0
    #: Runs one sweep may re-dispatch. Bounded like every other query here.
    worker_sweep_batch: int = 100
    #: How long a stopping worker waits for its runs to reach a checkpoint
    #: before cancelling them. A cancelled run is handed back, not failed.
    worker_shutdown_grace_seconds: float = 30.0

    # --- agents (Phase 10) -------------------------------------------------
    #: Pages one researcher fetches and ingests at once. Multiplied by
    #: ``graph_max_concurrency``, since that many researchers run in parallel:
    #: the product is the run's concurrent fetches and database sessions, and it
    #: has to stay under the connection pool.
    researcher_fetch_concurrency: int = 2
    #: Search results requested per query, before deduplication and selection.
    researcher_results_per_query: int = 10
    #: Retrieved passages one evidence-extraction call reads, and how many such
    #: calls a round may make. Together they bound how much of a large round is
    #: read at all, so a round that exceeds them says so in the log.
    evidence_passages_per_call: int = 12
    evidence_max_calls_per_round: int = 3

    # Per-user guardrails on the API surface itself.
    max_concurrent_runs_per_user: int = 3
    default_page_size: int = 20
    max_page_size: int = 100

    # --- authentication and sessions (Phase 20, FR-1, ADR 0021) -----------
    #: Whether ``POST /auth/register`` creates accounts. A deployment that
    #: provisions its users some other way turns this off and the endpoint
    #: refuses, rather than being left open because nobody thought to close it.
    registration_enabled: bool = True
    #: Whether ``X-Aether-User`` and the default developer principal work at
    #: all. Honoured only inside the environment allowlist in
    #: ``app.auth.principal``, which is checked first - so this can close the
    #: development gate further but can never open it. It exists so the tests
    #: that exercise real sign-in can switch the affordance off without
    #: pretending to be a different environment, since `APP_ENV` also chooses
    #: the queue, the cache, the storage backend and the rate-limit backend.
    dev_identity_enabled: bool = True
    #: Floor on a new password. NIST 800-63B's own guidance: length is the
    #: control that works, composition rules are not required. Refused below 8.
    min_password_length: int = 12
    #: Argon2id cost. The library's defaults, which are RFC 9106's low-memory
    #: profile and OWASP's first recommendation. A deployment on bigger
    #: hardware should raise them; `check_needs_rehash` re-hashes existing
    #: passwords on the next successful login, so raising them locks nobody out.
    password_hash_time_cost: int = 3
    password_hash_memory_kib: int = 64 * 1024
    password_hash_parallelism: int = 4

    session_cookie_name: str = "aether_session"
    #: Unset means a host-only cookie, which is what a single-host deployment
    #: wants. Set it to a parent domain only to share the session across
    #: subdomains, and understand that every subdomain then receives it.
    session_cookie_domain: str | None = None
    #: ``lax`` is enough when the web app and the API share a registrable
    #: domain. ``none`` is for the deployment that genuinely splits them, and
    #: browsers only honour it with ``Secure`` - which the validator enforces.
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    #: ``None`` resolves by environment: on everywhere but `local` and `test`.
    #: Explicit configuration always wins, so an HTTPS local setup can say so.
    session_cookie_secure: bool | None = None
    #: How long a session lives. Fourteen days: long enough that people are not
    #: signing in constantly, short enough that a token stolen from a machine
    #: nobody uses any more stops working.
    session_ttl_seconds: int = 14 * 24 * 3600
    #: Live sessions one account may hold. Signing in on one more revokes the
    #: oldest rather than refusing, so a person is never locked out by it.
    max_sessions_per_user: int = 10

    #: How many proxies sit in front of this process. Zero - the default -
    #: means the socket's peer address is the client, and ``X-Forwarded-For``
    #: is ignored entirely. A non-zero value is a statement that exactly that
    #: many trusted hops append to the header, and the client address is read
    #: that many entries from the right. Trusting the header by its presence
    #: would let any caller choose their own rate-limit bucket and forge the
    #: address written into the audit log.
    trusted_proxy_hops: int = 0

    # --- rate limiting (Phase 20, TDD 3.2) --------------------------------
    #: Token buckets, per user (or per client address when unauthenticated)
    #: and per route class. `burst` is the bucket's capacity - what a client
    #: may spend at once after being idle - and `per_minute` is the refill
    #: rate, which is the sustained ceiling.
    rate_limit_enabled: bool = True
    rate_limit_read_per_minute: int = 120
    rate_limit_read_burst: int = 60
    #: Writes are what start work: a run, a follow-up, an upload. Tighter,
    #: because the cost of one is not one request.
    rate_limit_write_per_minute: int = 20
    rate_limit_write_burst: int = 10
    #: Unauthenticated credential endpoints. Tightest of the three, and the
    #: only one that is also keyed by the address being attempted, so that
    #: stuffing one account from many addresses is bounded as well.
    rate_limit_auth_per_minute: int = 10
    rate_limit_auth_burst: int = 5

    # --- SSE --------------------------------------------------------------
    sse_heartbeat_seconds: int = 15
    # A stream that outlives this is closed; the browser reconnects with
    # Last-Event-ID, which is cheaper than holding a connection open forever.
    sse_max_connection_seconds: int = 900
    sse_replay_buffer_size: int = 500
    #: How long the Redis fan-out keeps a run's buffered events and its
    #: counter. Long enough to cover a reconnect during a run, short enough
    #: that a finished run is not Redis' problem: the durable log below is
    #: what a client reconnecting hours later is served from.
    sse_event_buffer_ttl_seconds: int = 3600
    #: Whether every streamed event is also written to `research_events`
    #: (ADR 0006). Turning it off makes replay depend on the Redis buffer,
    #: which is bounded and expires - a real trade, so it is a real setting
    #: rather than something that happens when a dependency is missing.
    persist_research_events: bool = True

    # --- observability (Phase 17) -----------------------------------------
    #: Where spans are exported. Unset means no tracer provider is installed
    #: at all: every span in the code still runs against the API's no-op
    #: implementation, costs almost nothing and records nothing, which is why
    #: there is no "is tracing on" check anywhere outside `app.observability`.
    otel_exporter_otlp_endpoint: str | None = None
    otel_service_name: str = "aether-api"
    #: Whether this process collects and exposes Prometheus metrics. On by
    #: default: they are cheap, and a deployment that cannot see its own
    #: failure rate is one nobody can operate.
    metrics_enabled: bool = True
    #: The worker serves nothing else, so it needs a socket of its own for
    #: Prometheus to pull from. Matches `infra/monitoring/prometheus.yml`.
    worker_metrics_port: int = 9100

    #: LangSmith. Off unless asked for, and asked for here rather than through
    #: the library's own environment variables: it reads those directly, past
    #: the typed settings layer, so prompts and retrieved documents would
    #: otherwise leave the system for a third party whenever a variable
    #: happened to be set in a shell (found in Phase 9).
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "aether-research"

    # --- caching (Phase 15, TDD 13) ---------------------------------------
    #: Whether anything is stored. Off still coalesces simultaneous identical
    #: calls: doing one piece of work once and remembering it afterwards are
    #: two different promises, and only the second is optional.
    cache_enabled: bool = True
    #: Short: a search answer goes stale, and the point is to avoid paying
    #: twice for the same query inside one run or between two near each other.
    cache_search_ttl_seconds: int = 6 * 3600
    #: Medium: a fetched page and the article extracted from it. Both are
    #: keyed by content, so a page that changes gets a new key rather than a
    #: stale value - the TTL is about storage, not about correctness.
    cache_page_ttl_seconds: int = 3 * 24 * 3600
    #: Long: an embedding is a deterministic function of its text and model.
    cache_embedding_ttl_seconds: int = 30 * 24 * 3600
    #: Whether embeddings are cached at all. The key is a hash of the exact
    #: text, so a lookup requires already having the content - but a
    #: deployment that would rather not cache a function of user documents at
    #: all turns it off here, and pays for the vectors again.
    cache_embeddings: bool = True
    #: Refuse to store a value larger than this. A whole large page is not
    #: worth a cache key, and accepting one is how a cache fills up with the
    #: few entries that never get read.
    cache_max_value_bytes: int = 1024 * 1024
    #: Entries the in-process backend holds. Bounded, because these are pages
    #: and vectors rather than counters.
    cache_max_entries: int = 2048

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
    #: Whether a run under a cost ceiling may call a model the registry does
    #: not price. Refused by default (Phase 16): a run whose spend cannot be
    #: measured cannot be held to a limit, and the limit is the promise. The
    #: refusal fails over to the next model in the chain, so a chain with one
    #: priced model still works. Turn it off for a deployment that would
    #: rather run unpriced models and accept an unenforceable ceiling.
    require_priced_models: bool = True

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

    # --- document ingestion (Phase 7, ADR 0012) ---------------------------
    #: Largest file the upload endpoint accepts, enforced while the body is
    #: read rather than after it is buffered. ``None`` means the artifact
    #: ceiling: an upload that could not be stored must not be accepted.
    max_upload_bytes: int | None = None

    #: Each document is parsed in a child process that is killed at this
    #: deadline (threat model 3.6). Not a retry budget: a document that hangs
    #: the parser once hangs it every time.
    parse_timeout_seconds: float = 60.0
    #: Parser processes one worker may run at once. Parsing is CPU-bound, so
    #: more than the core count only adds contention.
    max_concurrent_parses: int = 2
    #: Address-space ceiling for the parser process, where the OS has one.
    parse_max_memory_bytes: int = 2 * 1024 * 1024 * 1024
    max_pdf_pages: int = 1000
    #: Normalised characters per document. Bounds chunking time - linear in
    #: size, but slowest on unbroken text - and the embedding bill.
    max_document_chars: int = 2_000_000

    #: Chunk size and overlap in cl100k tokens. Why these values, and how they
    #: will be measured rather than asserted, is recorded in ADR 0012.
    chunk_size_tokens: int = 512
    chunk_overlap_tokens: int = 64
    max_chunks_per_document: int = 2000
    #: Texts per embedding request. Larger batches amortise the round trip;
    #: smaller ones lose less work to a failure.
    embedding_batch_size: int = 64

    # --- retrieval (Phase 8, ADR 0013) ------------------------------------
    #: Chunks a retrieval call returns, and candidates each arm fetches before
    #: fusion - the TDD's "top ~50 -> top ~8-12". Both are documented starting
    #: points that `python -m app.retrieval.benchmark` measures, not tuned
    #: values; the plan's own bounds are in app/retrieval/query.py.
    retrieval_limit: int = 10
    retrieval_candidates: int = 50
    #: Reciprocal Rank Fusion's rank constant, from the paper that introduced it.
    retrieval_rrf_k: int = 60
    #: Relative trust in each arm. A deployment whose corpus the lexical index
    #: cannot stem, or one with no embeddings, tilts these rather than editing
    #: code - and setting one to zero disables that arm honestly, as a skipped
    #: arm on the result rather than as a silently shorter list.
    retrieval_dense_weight: float = 1.0
    retrieval_lexical_weight: float = 1.0
    #: Whether the configured reranker runs, and how far it trades relevance for
    #: coverage. 1.0 is pure relevance; see app/retrieval/rerank.py.
    retrieval_rerank: bool = True
    retrieval_mmr_lambda: float = 0.7

    @field_validator(
        "openai_api_key",
        "anthropic_api_key",
        "tavily_api_key",
        "brave_api_key",
        "langsmith_api_key",
        "github_token",
        "s3_access_key_id",
        "s3_secret_access_key",
        mode="before",
    )
    @classmethod
    def _blank_credential_is_no_credential(cls, value: object) -> object:
        """A variable set to nothing means the credential is absent, not empty.

        `.env.example` ships every key blank, so `make env` produces a file in
        which `OPENAI_API_KEY=` is present with no value. Read as a value that is
        a `SecretStr("")`, which is not `None` - so a provider was built with an
        empty key and the vendor SDK refused it in its constructor, crashing the
        process at startup instead of simply not having OpenAI. Found by starting
        a worker from the shipped example (Phase 13).

        Normalised here rather than at each use site, because every one of those
        sites already asks the only question that matters - is there a
        credential - and each would otherwise have to ask it twice.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("session_cookie_secure", "session_cookie_domain", mode="before")
    @classmethod
    def _blank_cookie_setting_is_unset(cls, value: object) -> object:
        """A variable set to nothing means "not configured", not "empty".

        The same finding as the blank-credential rule below, on two settings
        where it fails differently. `.env.example` ships every optional key
        present and blank, so `make env` produces `SESSION_COOKIE_SECURE=` -
        which pydantic cannot read as a boolean at all, so the process refuses
        to start. `SESSION_COOKIE_DOMAIN=` is worse, because it parses: an
        empty domain would be emitted as a bare `Domain=` on every
        `Set-Cookie`, and a browser's handling of that is not something to find
        out in production.

        Both now resolve the way the file says they do - Secure by environment,
        and a host-only cookie. Found by the test that parses the shipped
        example, which exists because Phase 13 shipped a `.env.example` that
        crashed a worker at startup.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

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

    @field_validator("cors_allow_origins", mode="after")
    @classmethod
    def _reject_wildcard_origin(cls, value: list[str]) -> list[str]:
        """A wildcard origin is refused, because this API sends credentials.

        Starlette treats `allow_origins=["*"]` with `allow_credentials=True` by
        echoing whatever Origin the request carried - so every website a user
        visits could make authenticated requests on their behalf. Browsers do
        not stop it, because the response looks like a specific-origin grant.

        Refusing at startup rather than documenting it: `CORS_ALLOW_ORIGINS=*`
        is exactly what someone types when a deployment's CORS is "not working",
        and it must not be the thing that quietly succeeds.
        """
        if any(origin.strip() == "*" for origin in value):
            raise ValueError(
                "CORS_ALLOW_ORIGINS cannot be '*': this API is called with "
                "credentials, and a wildcard origin would let any site make "
                "authenticated requests. List the exact origins instead."
            )
        return value

    @model_validator(mode="after")
    def _check_ingestion_bounds(self) -> Settings:
        """Refuse ingestion limits that contradict each other.

        Each of these is a deployment that starts, accepts work, and then fails
        on it: an upload ceiling above the artifact ceiling accepts files that
        cannot be stored, and an overlap as large as the chunk never advances.
        """
        if self.max_upload_bytes is not None and not (
            0 < self.max_upload_bytes <= self.max_artifact_bytes
        ):
            raise ValueError(
                "MAX_UPLOAD_BYTES must be positive and cannot exceed MAX_ARTIFACT_BYTES: "
                "an accepted upload could not be stored."
            )
        if not 0 <= self.chunk_overlap_tokens < self.chunk_size_tokens:
            raise ValueError(
                "CHUNK_OVERLAP_TOKENS must be at least 0 and smaller than CHUNK_SIZE_TOKENS."
            )
        for name in (
            "max_concurrent_parses",
            "max_pdf_pages",
            "max_document_chars",
            "max_chunks_per_document",
            "embedding_batch_size",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name.upper()} must be at least 1.")
        if self.parse_timeout_seconds <= 0:
            raise ValueError("PARSE_TIMEOUT_SECONDS must be positive.")
        return self

    @model_validator(mode="after")
    def _check_graph_bounds(self) -> Settings:
        """Refuse run and graph ceilings that could not bound anything.

        A ceiling of zero is not "unlimited" here - it is a run that stops before
        it starts - and a negative one is a typo. Both are refused at startup.

        ``20`` is restated from ``app.agents.schemas.MAX_PLANNED_SUBTASKS``
        rather than imported, for the same reason the retrieval bounds are: this
        module is a leaf. A test asserts the two agree.
        """
        for name in (
            "max_research_iterations",
            "max_sources",
            "max_search_queries",
            "max_runtime_seconds",
            "max_subtasks_per_iteration",
            "graph_max_concurrency",
            "checkpoint_pool_size",
            "researcher_fetch_concurrency",
            "researcher_results_per_query",
            "evidence_passages_per_call",
            "evidence_max_calls_per_round",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name.upper()} must be at least 1.")
        if self.max_estimated_cost_usd <= 0:
            raise ValueError("MAX_ESTIMATED_COST_USD must be positive.")
        if self.graph_node_timeout_seconds <= 0:
            raise ValueError("GRAPH_NODE_TIMEOUT_SECONDS must be positive.")
        if self.max_subtasks_per_iteration > 20:
            raise ValueError(
                "MAX_SUBTASKS_PER_ITERATION cannot exceed 20, the most subtasks a plan may propose."
            )
        return self

    @model_validator(mode="after")
    def _check_worker_bounds(self) -> Settings:
        """Refuse a worker configuration that would take runs away from itself.

        The lease is the interesting one. A worker renews it at every node
        boundary, and a node may run for ``graph_node_timeout_seconds``, so a
        lease shorter than that expires while the worker is healthy and another
        worker takes the run mid-node. Both then hold it. Twice the node timeout
        leaves room for the node plus the write that follows it.
        """
        for name in ("worker_concurrency", "worker_max_attempts", "worker_sweep_batch"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name.upper()} must be at least 1.")
        for name in (
            "worker_poll_seconds",
            "worker_retry_base_delay_seconds",
            "worker_retry_max_delay_seconds",
            "worker_sweep_interval_seconds",
            "worker_queued_grace_seconds",
            "worker_shutdown_grace_seconds",
        ):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name.upper()} must be positive.")
        if self.worker_retry_max_delay_seconds < self.worker_retry_base_delay_seconds:
            raise ValueError(
                "WORKER_RETRY_MAX_DELAY_SECONDS cannot be below WORKER_RETRY_BASE_DELAY_SECONDS."
            )
        if self.worker_lease_seconds < 2 * self.graph_node_timeout_seconds:
            raise ValueError(
                "WORKER_LEASE_SECONDS must be at least twice GRAPH_NODE_TIMEOUT_SECONDS: a "
                "shorter lease expires while a node is still running and lets a second worker "
                "take the run."
            )
        return self

    @model_validator(mode="after")
    def _check_auth_bounds(self) -> Settings:
        """Refuse an authentication configuration that would not authenticate.

        Each of these is a deployment that starts and then hands out sessions
        nobody can use, or protects them with something that is not protection:
        a cookie a browser silently drops, a password floor below what is worth
        hashing, or Argon2 parameters under the profile they are named for.
        """
        if self.session_cookie_samesite == "none" and not self.session_cookie_is_secure:
            raise ValueError(
                "SESSION_COOKIE_SAMESITE=none requires a Secure cookie: browsers "
                "reject SameSite=None without it, so no session would ever be set. "
                "Set SESSION_COOKIE_SECURE=true, or keep SameSite=lax."
            )
        if self.session_ttl_seconds < 60:
            raise ValueError("SESSION_TTL_SECONDS must be at least 60.")
        if self.max_sessions_per_user < 1:
            raise ValueError("MAX_SESSIONS_PER_USER must be at least 1.")
        if self.min_password_length < 8:
            raise ValueError(
                "MIN_PASSWORD_LENGTH cannot be below 8: a shorter floor is not a "
                "password policy, whatever the hash costs."
            )
        if self.trusted_proxy_hops < 0:
            raise ValueError("TRUSTED_PROXY_HOPS cannot be negative.")
        # RFC 9106's low-memory profile, which is what the defaults are. Below
        # any of these, the hash is weaker than the thing it is named after.
        if self.password_hash_time_cost < 2:
            raise ValueError("PASSWORD_HASH_TIME_COST must be at least 2.")
        if self.password_hash_memory_kib < 19 * 1024:
            raise ValueError("PASSWORD_HASH_MEMORY_KIB must be at least 19456 (19 MiB).")
        if self.password_hash_parallelism < 1:
            raise ValueError("PASSWORD_HASH_PARALLELISM must be at least 1.")
        for name in (
            "rate_limit_read_per_minute",
            "rate_limit_read_burst",
            "rate_limit_write_per_minute",
            "rate_limit_write_burst",
            "rate_limit_auth_per_minute",
            "rate_limit_auth_burst",
        ):
            if getattr(self, name) < 1:
                raise ValueError(
                    f"{name.upper()} must be at least 1. To switch rate limiting off, "
                    "set RATE_LIMIT_ENABLED=false - which is a decision, not a typo."
                )
        return self

    @model_validator(mode="after")
    def _check_retrieval_bounds(self) -> Settings:
        """Refuse a retrieval configuration that cannot answer what it promises.

        Restated here rather than imported from ``RetrievalPlan``, which is the
        authority on them: settings is a leaf that the retrieval layer reads,
        and importing back into it would invert that and drag the ingestion
        modules into every process that reads configuration. A test asserts the
        two agree, which is the part that would otherwise drift.
        """
        if self.retrieval_candidates < self.retrieval_limit:
            raise ValueError(
                "RETRIEVAL_CANDIDATES must be at least RETRIEVAL_LIMIT: fusion cannot "
                "return more chunks than the arms fetched."
            )
        if not self.retrieval_dense_weight and not self.retrieval_lexical_weight:
            raise ValueError(
                "RETRIEVAL_DENSE_WEIGHT and RETRIEVAL_LEXICAL_WEIGHT cannot both be zero: "
                "that configuration would search nothing."
            )
        for name in ("retrieval_limit", "retrieval_candidates", "retrieval_rrf_k"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name.upper()} must be at least 1.")
        for name in ("retrieval_dense_weight", "retrieval_lexical_weight"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name.upper()} cannot be negative.")
        if not 0.0 <= self.retrieval_mmr_lambda <= 1.0:
            raise ValueError("RETRIEVAL_MMR_LAMBDA must be between 0 and 1.")
        return self

    @property
    def upload_limit_bytes(self) -> int:
        """The upload ceiling actually enforced."""
        return self.max_upload_bytes or self.max_artifact_bytes

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def session_cookie_is_secure(self) -> bool:
        """Whether the session cookie carries ``Secure``.

        Explicit configuration wins; otherwise on everywhere but the two
        environments that are served over plain HTTP. The environment names are
        restated here rather than imported from ``app.auth.principal``, which
        owns the same list for the development identity: this module is a leaf
        that the auth layer reads, and importing back into it would invert
        that. ``test_security_hardening.py`` asserts the two agree, which is
        the part that would otherwise drift.
        """
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        return self.app_env not in ("local", "test")

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


#: Operating-system variables the document parser's child process inherits
#: (ADR 0012). An allowlist, not a denylist: a secret added to the deployment
#: later is withheld from the parser without anyone having to remember it here.
PARSER_ENVIRONMENT_ALLOWLIST = (
    "PATH",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
)


def parser_environment() -> dict[str, str]:
    """The environment for the parser child process: the allowlist, nothing else.

    Here rather than beside the parser because this module is the one place
    that reads the process environment, so auditing what leaves the process
    means reading one file. It reads in order to withhold: the worker's
    environment carries the database URL and every provider key, and code
    running in the parser should find none of them.
    """
    return {name: os.environ[name] for name in PARSER_ENVIRONMENT_ALLOWLIST if name in os.environ}
