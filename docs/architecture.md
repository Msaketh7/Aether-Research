# Aether Research: Architecture

> This is the **map**. The [TDD](TDD.md) is the territory - it carries the full
> component design, schema and failure analysis. Read this first to know where
> things live and why; go to the TDD for detail; go to [ADRs](ADRs/) for the
> reasoning behind each irreversible choice.

## 1. The shape of the system

Aether is a **modular monolith** ([ADR 0001](ADRs/0001-modular-monolith.md)):
one codebase, two process types, three backing services.

```
                    browser (Next.js, apps/web)
                       |  fetch (REST)        |  EventSource (SSE)
                       v                      v
        +-------------------------------------------------+
        |  api process  -  FastAPI (apps/api)              |
        |  auth | validation | authz | rate limit | SSE    |
        +-------------------------------------------------+
             |  enqueue run_id            ^  relay events
             v                            |
        +----------+   Redis   +----------------------------+
        |  queue   |---------->|  worker process            |
        |  cache   |           |  LangGraph research graph  |
        |  pub/sub |<----------|  agents | tools | retrieval|
        +----------+           +----------------------------+
                                     |            |
                          PostgreSQL + pgvector   S3 / MinIO
                          (system of record)      (raw artifacts)
```

**The API never runs a research graph.** It validates, persists, enqueues and
returns `202 Accepted` with a run id. A multi-minute workflow inside a request
thread is the single failure mode this architecture exists to prevent.

## 2. Process types

| Process  | Responsibility                                             | Scales on           |
| -------- | ---------------------------------------------------------- | ------------------- |
| `web`    | Next.js UI, server-rendered shell, no secrets              | request rate        |
| `api`    | REST + SSE, auth, authz, validation, enqueue               | request concurrency |
| `worker` | LangGraph execution, agents, tools, retrieval, persistence | queue depth         |

`api` and `worker` are built from **one image** with different entrypoints, so
they share models, migrations and configuration.

## 3. Module boundaries (`apps/api/app/`)

| Module           | Owns                                                               |
| ---------------- | ------------------------------------------------------------------ |
| `api/`           | HTTP routing, request/response DTOs, SSE relay                     |
| `core/`          | settings, logging, errors, typed config                            |
| `auth/`          | sessions, password hashing, authz helpers                          |
| `research/`      | run lifecycle, status transitions, the progress event bus          |
| `agents/`        | LangGraph nodes and agent implementations                          |
| `retrieval/`     | `Retriever` interface, hybrid search, reranking                    |
| `sources/`       | connectors, fetch pipeline, SSRF guard, dedupe                     |
| `evidence/`      | claim/evidence/contradiction domain logic                          |
| `reports/`       | report schema, synthesis assembly, citation validation             |
| `evaluations/`   | benchmark runner, metrics, thresholds                              |
| `observability/` | tracing, metrics, structured logging                               |
| `cache/`         | content-hash response cache, TTL policy, single-flight             |
| `db/`            | SQLAlchemy models, repositories, migrations                        |
| `workers/`       | queue consumer, job lifecycle, checkpoint recovery, event emission |

Modules talk through typed service interfaces. A module never imports another
module's ORM models directly.

## 4. The research graph

```
START
  -> Planner              decompose question into typed subtasks
  -> Research Fan-Out     parallel researchers (web / document / data)
  -> Evidence Extraction  claim + verbatim span + source
  -> Claim Normalization  atomic statements, dedupe key
  -> Verification         corroboration, calibrated confidence
  -> Contradictions       disagreements recorded, never resolved silently
  -> Critic               is coverage sufficient?
       |-- insufficient and within limits --> back to Planner (bounded)
       '-- sufficient or limit reached ----v
  -> Synthesis            structured report from evidence only
  -> Citation Validation  every [n] resolves to real evidence
END
```

Every loop is bounded by `max_iterations`, `max_sources`, `max_search_queries`,
`max_runtime` and `max_estimated_cost`, checked at every node boundary, and every
node by a timeout. Hitting a limit is a normal outcome: discovery stops, the run
proceeds to synthesis, and the report carries an explicit coverage caveat.
Cancellation stops the run at the next node. The graph checkpoints after every
node and resumes from the last checkpoint (ADR 0014).

## 5. Data flow of one claim

This is the traceability chain the whole product is built around:

```
search hit -> Source (url, publisher, content_hash, accessed_at)
           -> Document (normalized text in Postgres, raw bytes in S3)
           -> Chunk (embedding + tsvector)
           -> Evidence (verbatim span, char offsets, stance)
           -> Claim (normalized statement, confidence, status)
           -> Citation ([n] in a report section)
```

The citation validator walks this chain backwards. A citation whose chain breaks
at any link is rejected before the report is returned - which is the mechanism
that makes "never fabricate citations" a property of the system rather than a
hope about the model.

## 6. Frontend architecture (`apps/web`)

- Next.js App Router; server components for shells, client components for
  anything interactive or streaming.
- **TanStack Query** owns all server state. No global store.
- One typed API client. Components never call `fetch` directly.
- SSE via native `EventSource`, reconciled into the query cache so the live feed
  and the REST snapshot cannot disagree.
- `NEXT_PUBLIC_API_MODE` selects the transport target
  ([ADR 0009](ADRs/0009-frontend-mock-transport.md)); no other code changes when
  the real backend lands.
- No secret ever reaches client code. Provider keys live in the API process.

## 7. Cross-cutting rules

| Rule                                                                  | Where enforced                          |
| --------------------------------------------------------------------- | --------------------------------------- |
| Every external call has a timeout, retry policy and error class       | `sources/`, `models/`, `retrieval/`     |
| Every LLM call is recorded (tokens, cost, latency, model, run, agent) | LLM gateway                             |
| Every tool call is recorded (request, status, latency, cache hit)     | tool wrapper                            |
| Web content is untrusted data, never instructions                     | `sources/` sanitizer + prompt structure |
| Every list query is bounded and paginated                             | repository layer                        |
| Every research object is checked for ownership                        | `auth/` dependency                      |
| Run state is durable and resumable                                    | LangGraph checkpointer + Postgres       |

## 8. Delivery phases

Phases build vertically: frontend prototype, backend foundation, data layer, LLM
layer, RAG, agents, evaluation, observability, load handling, deployment. Each
phase ships something runnable and tested before the next begins. Current
status is tracked in the root [README](../README.md).
