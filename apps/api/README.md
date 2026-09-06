# aether-api

The Aether Research backend: one codebase, two process types
([ADR 0001](../../docs/ADRs/0001-modular-monolith.md)).

| Process  | Entry point          | Responsibility                                                                          |
| -------- | -------------------- | --------------------------------------------------------------------------------------- |
| `api`    | `app.main:app`       | HTTP + SSE. Validates, authorises, persists, enqueues. **Never runs a research graph.** |
| `worker` | `app.workers.runner` | Consumes the queue and executes the LangGraph workflow (Phase 9/13).                    |

## Status

**Phase 3 (data layer) is complete.** What exists today:

- typed settings, structured JSON logging, request-id propagation
- the error envelope the frontend already consumes (`ApiErrorBody`)
- liveness and readiness probes that really check Postgres and Redis
- the research API surface from `docs/TDD.md` section 18, with `202 Accepted`
  on create and an SSE progress stream
- per-user ownership enforced in the SQL of every read
- **PostgreSQL persistence**: 19 tables, Alembic migrations, keyset pagination,
  a session-per-request transaction, and connection pooling. A run survives a
  restart of this process.

What does **not** exist yet, and is not pretended to:

- **A worker.** A created run is enqueued and stays `queued`; nothing consumes
  the queue until Phase 13, and there is no graph to run until Phase 9. The API
  reports this honestly rather than faking progress.
- **Rows for anything a run has not produced.** The tables for sources,
  evidence, reports and traces exist and are constrained, but only
  `research_runs` and `users` are written to so far. The endpoints return empty
  collections, which is the truthful answer.

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
├── observability/  request-id and access-log middleware
└── agents/ retrieval/ sources/ evidence/ reports/ evaluations/
                    module boundaries, filled by later phases
```

Modules talk through typed service interfaces and never import another module's
ORM models directly.
