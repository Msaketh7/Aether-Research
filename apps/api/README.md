# aether-api

The Aether Research backend: one codebase, two process types
([ADR 0001](../../docs/ADRs/0001-modular-monolith.md)).

| Process  | Entry point          | Responsibility                                                                          |
| -------- | -------------------- | --------------------------------------------------------------------------------------- |
| `api`    | `app.main:app`       | HTTP + SSE. Validates, authorises, persists, enqueues. **Never runs a research graph.** |
| `worker` | `app.workers.runner` | Consumes the queue and executes the LangGraph workflow (Phase 9/13).                    |

## Status

**Phase 6 (web research tools) is complete.** What exists today:

- typed settings, structured JSON logging, request-id propagation
- the error envelope the frontend already consumes (`ApiErrorBody`)
- liveness and readiness probes that really check Postgres, Redis and the
  artifact store
- the research API surface from `docs/TDD.md` section 18, with `202 Accepted`
  on create and an SSE progress stream
- per-user ownership enforced in the SQL of every read
- **PostgreSQL persistence**: 19 tables, Alembic migrations, keyset pagination,
  a session-per-request transaction, and connection pooling. A run survives a
  restart of this process.
- **Object storage** behind `ObjectStorage`
  ([ADR 0010](../../docs/ADRs/0010-object-storage.md)): S3 and MinIO through one
  implementation, a filesystem backend for development without Docker, a derived
  key namespace, and timeouts, retries and a size ceiling on every call.
- **Six research tools** behind a `Toolbelt`
  ([ADR 0011](../../docs/ADRs/0011-untrusted-content-boundary.md)): web search,
  fetch, extract, SEC EDGAR, arXiv and GitHub, each with a strict input schema,
  a timeout, retries, error classification and a recorded call — behind a
  four-layer SSRF guard and an untrusted-content type.
- **A model gateway** behind `LLMGateway`
  ([ADR 0007](../../docs/ADRs/0007-model-routing.md)): Anthropic, OpenAI and
  Ollama behind one interface, a YAML model registry, role- and mode-based
  routing with declared fallbacks, and bounds and a call ledger on every
  request.

What does **not** exist yet, and is not pretended to:

- **A worker.** A created run is enqueued and stays `queued`; nothing consumes
  the queue until Phase 13, and there is no graph to run until Phase 9. The API
  reports this honestly rather than faking progress.
- **Rows for anything a run has not produced.** The tables for sources,
  evidence, reports and traces exist and are constrained, but only
  `research_runs` and `users` are written to so far. The endpoints return empty
  collections, which is the truthful answer.
- **Anything written to the artifact store.** The storage layer is built and
  tested, and nothing calls it yet: the first artifact is written by the
  ingestion pipeline in Phase 7. The bucket being empty is the truthful state,
  not a failure.
- **Any model call.** The gateway is built and tested; the agents that call it
  arrive in Phase 10. Nothing in this build sends a prompt to a provider, so no
  API key is required to run it.
- **Any research.** The tools are built and tested; nothing calls them until the
  agent graph exists (Phases 9-10). A created run still stays `queued`.

## Database

The schema is `docs/TDD.md` section 7.2. Two migrations:

| Revision                   | Contents                                                                                       |
| -------------------------- | ---------------------------------------------------------------------------------------------- |
| `0001_core_schema`         | citext and pgcrypto, then all 19 tables with their indexes, foreign keys and check constraints |
| `0002_pgvector_embeddings` | `CREATE EXTENSION vector`, `document_chunks.embedding`, and its HNSW cosine index              |

They are separate because pgvector is a _server-side prerequisite_: a managed
Postgres may need an operator to enable it first. Isolating it means the
relational schema deploys and verifies on its own, and a missing extension
fails in one place with an actionable error instead of taking the whole schema
down with it.

```bash
make migrate                       # upgrade head
make migration m="add x"           # autogenerate from the models
make migrate-check                 # upgrade -> downgrade -> upgrade round trip
```

### Testing against a real database

The suite refuses to test SQL against anything but Postgres. It finds one in
this order:

1. `AETHER_TEST_DATABASE_URL` - what CI sets, pointing at a `pgvector/pgvector`
   service container.
2. A **throwaway cluster** built with the locally installed `initdb`/`pg_ctl`,
   on a random port in a temp directory with trust auth, destroyed afterwards.
   It touches no existing cluster and needs no credentials.
3. Neither - the database tests skip with an explicit reason rather than
   silently passing.

If the available server has no pgvector, the suite migrates to
`0001_core_schema` and the vector-specific assertions skip while the schema
drift test still asserts that the embedding column is the _only_ difference.

## Research tools

Six tools, and only six. There is no shell tool, no filesystem tool and no
code-execution tool — not disabled, absent (TDD 15.3).

| Tool              | Does                                  |
| ----------------- | ------------------------------------- |
| `web_search`      | candidate sources via Tavily or Brave |
| `fetch_url`       | retrieve a page, guarded and bounded  |
| `extract_content` | readability pass, boilerplate removed |
| `search_sec`      | EDGAR full-text search over filings   |
| `search_arxiv`    | preprints                             |
| `search_github`   | repositories and code                 |

An agent is handed a `Toolbelt`, which is a **capability**: which tools it
carries is decided by the caller's role, and the synthesizer's carries none.

### Two controls enforced by the type system

**Retrieved text cannot become an instruction.** It is `UntrustedText`, not
`str`, and `__str__` raises:

```python
prompt = f"Summarise: {page.body}"  # TypeError, at the moment it is written
prompt = page.body.for_prompt()  # delimited, with a standing data notice
raw = page.body.expose()  # storage, hashing, span verification
```

**Every outbound request goes through one guarded client.** Its SSRF guard runs
in four layers, and each exists because the one before it can be defeated:

1. scheme allowlist, no URL credentials, dangerous ports refused;
2. DNS resolved before the request — the hostname is never trusted, and
   `http://2130706433/`, `http://127.1/` and `http://0177.0.0.1/` are decoded
   as the loopback addresses they are;
3. **every** resolved address checked, not the first;
4. the **connected peer** verified against that set before any body is read,
   which closes the DNS-rebinding window.

Redirects are followed manually and re-validated at every hop; the cookie jar is
emptied before every request. `robots.txt` is honoured, cached per origin.

### Testing

The SSRF cases are written as _attacks_, not as coverage — each is a technique
that defeats a naive guard, and the docstring names it. The fetcher runs against
`httpx2.MockTransport`, so redirect handling, streaming and the size cap all
really execute. What is **not** covered, stated rather than implied: whether the
vendors' current response shapes still match the fixtures. A live smoke test
belongs in Phase 19.

## Model gateway

Agents ask for a **role**, never a model:

```python
completion = await gateway.generate(role=AgentName.PLANNER, mode=ResearchMode.DEEP, prompt=prompt)
```

Declared models live in [`app/models/registry.yaml`](app/models/registry.yaml) —
provider, tier, context window, capability flags and a dated, sourced price.
Adding a model or repricing one is a reviewable diff, not a deploy.

| Concern                | Where it is handled                                            |
| ---------------------- | -------------------------------------------------------------- |
| which model for a role | `routing.py`: role → tier, shifted by research mode            |
| a provider failing     | bounded retry with jitter, then failover down a declared chain |
| fan-out                | a concurrency semaphore; the rest queue                        |
| what it cost           | every _attempt_ recorded with tokens, cost, latency and status |
| provider quirks        | capability flags on the model spec, never silent degradation   |

Two honesty rules the code enforces. A model whose price cannot be verified is
**unpriced**, and its cost is `None` rather than `0.00`. A capability a provider
does not have — Anthropic embeddings, OpenAI token counting — raises
`CapabilityNotSupported` rather than returning something plausible.

`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`: leave either empty and that provider is
simply not built. Ollama needs no key, which is what lets the whole graph run
locally.

### Testing the providers

Through the **real vendor SDKs**, with only the socket replaced
(`httpx2.MockTransport`). Request building, serialisation, SSE and NDJSON stream
decoding and the SDKs' own exception classes all execute — which is where every
defect this phase found actually was. What is not covered, and is said rather
than implied: the real network, real authentication, and whether the vendors'
current responses still match the fixtures.

## Object storage

Keys are derived in `app/storage/keys.py` and nowhere else:

```
runs/{run_id}/{kind}/{name}          raw-html | pdf | document | screenshot | report
evaluations/{evaluation_id}/{name}
```

Immutable artifacts are content-addressed on the SHA-256 that
`documents.content_hash` stores, so re-ingesting a source converges on one
object instead of duplicating it.

| Environment                    | Backend                                | Selected by                   |
| ------------------------------ | -------------------------------------- | ----------------------------- |
| production / staging           | AWS S3, credentials from the task role | default                       |
| local development              | MinIO from `make up`                   | `S3_ENDPOINT_URL`             |
| tests, machines without Docker | a directory on disk                    | `APP_ENV=test`, or explicitly |

The filesystem backend is never selected implicitly outside tests and is refused
in production. `/ready` HEADs the configured bucket - not the account, which
would pass on credentials alone while every write failed.

### Testing against a real S3 endpoint

`moto` in **server** mode, on a free port, not its botocore-patching decorator:
request signing, addressing style, HTTP status handling and the streaming body
all really execute, which is where the bugs are. The same contract tests run
against both backends, so the filesystem one cannot be used to make a test pass
that S3 would fail.

## Running it

```bash
cd apps/api
uv sync                       # create .venv and install locked dependencies
uv run uvicorn app.main:app --reload --port 8000
```

Then `http://localhost:8000/docs` for the OpenAPI UI, `/health` for liveness.

Point the frontend at it with:

```bash
NEXT_PUBLIC_API_MODE=live
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1
```

## Checks

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy app
uv run pytest
```

## Layout

```
app/
├── api/            HTTP routing, DTOs, error handlers, SSE relay
│   └── v1/         health, auth, research, evaluations, settings
├── core/           settings, logging, error taxonomy, pagination
├── auth/           principal resolution and ownership checks
├── research/       run lifecycle, repository interface, event broker
├── db/             engine, session factory, health probe
├── workers/        job queue interface and adapters
├── storage/        ObjectStorage protocol, S3 and filesystem backends, keys
├── models/         LLM gateway, registry, routing, provider adapters
├── sources/        SSRF guard, guarded HTTP, untrusted content, the six tools
├── observability/  request-id and access-log middleware
└── agents/ retrieval/ sources/ evidence/ reports/ evaluations/
                    module boundaries, filled by later phases
```

Modules talk through typed service interfaces and never import another module's
ORM models directly.
