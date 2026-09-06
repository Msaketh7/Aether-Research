# aether-api

The Aether Research backend: one codebase, two process types
([ADR 0001](../../docs/ADRs/0001-modular-monolith.md)).

| Process  | Entry point          | Responsibility                                                                          |
| -------- | -------------------- | --------------------------------------------------------------------------------------- |
| `api`    | `app.main:app`       | HTTP + SSE. Validates, authorises, persists, enqueues. **Never runs a research graph.** |
| `worker` | `app.workers.runner` | Consumes the queue and executes the LangGraph workflow (Phase 9/13).                    |

## Status

**Phase 2 (backend foundation) is complete.** What exists today:

- typed settings, structured JSON logging, request-id propagation
- the error envelope the frontend already consumes (`ApiErrorBody`)
- liveness and readiness probes that really check Postgres and Redis
- the research API surface from `docs/TDD.md` section 18, with `202 Accepted`
  on create and an SSE progress stream
- per-user ownership enforced on every read
- a `ResearchRepository` interface with an in-memory adapter

What does **not** exist yet, and is not pretended to:

- **Persistence.** The in-memory repository is replaced by SQLAlchemy and
  Postgres in Phase 3. Restarting the API loses runs.
- **A worker.** A created run is enqueued and stays `queued`; nothing consumes
  the queue until Phase 13, and there is no graph to run until Phase 9. The API
  reports this honestly rather than faking progress.

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
