# AETHER RESEARCH

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

**Autonomous Multi-Agent Research Platform**

Ask a complex research question. Aether decomposes it, researches multiple
sources in parallel, verifies evidence, detects contradictions, and generates a
citation-grounded report with full traceability.

This is **not** "a chatbot that searches Google." It is a long-running, stateful
research workflow with parallel agents, persistent state, source/evidence
management, evaluation, observability, caching, rate limiting, and deployment.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## Plain-English summary (read this first)

**The problem.** Doing serious research (questions like "should we enter this
market?", "how do these competitors compare?", "what does the latest science
say?") means running dozens of searches, opening dozens of tabs, reading
everything, keeping track of which fact came from where, noticing when two
sources disagree, and finally writing it all up so someone else can trust it. It takes hours or days,
and the trail from "claim" back to "proof" usually gets lost.

**What Aether does.** You type one question. Aether acts like a **small team of
research analysts** that works for you:

| Role on a human research team                                                                              | The equivalent part of Aether     |
| ---------------------------------------------------------------------------------------------------------- | --------------------------------- |
| Lead analyst who breaks the big question into smaller ones and hands them out                              | **Planner**                       |
| Several junior analysts, each researching one sub-topic **at the same time**                               | **Researchers** (run in parallel) |
| Someone who highlights the exact sentence that proves each point and notes the source                      | **Evidence Extractor**            |
| A checker who confirms each fact appears in more than one trustworthy place                                | **Verification Agent**            |
| A checker who flags "these two sources disagree" instead of quietly picking one                            | **Contradiction Check**           |
| A demanding manager who asks "is this good enough?" and sends people back for more                         | **Critic**                        |
| The writer who turns all the findings into a clean, structured report                                      | **Synthesizer**                   |
| The fact-checker who makes sure every sentence in the report has a real source attached before it goes out | **Citation Validator**            |

**What you get back.** A structured report (executive summary, key findings,
detailed analysis, competitive landscape, risks, opportunities) in which
**every important statement has a clickable source and a confidence score**, and
where genuine disagreements between sources are shown rather than hidden.

**Why it is built the "hard" way.** Anyone can wire a search box to an AI model.
The engineering value here is everything around that: the work keeps running even
if a server restarts, it never spends more than a set budget of time and money,
it treats web pages as untrusted, it measures its own accuracy, and it can be
deployed and operated like a real product.

---

## Example

> **Question:** "Should our company invest in building an AI inference
> infrastructure business? Compare the market, competitors, technology, pricing,
> funding, recent developments, risks, and opportunities."

Aether turns that into seven parallel research streams (market, competitor,
technology, pricing, financial, recent news, risk), pulls from web search, SEC
EDGAR (official company filings), arXiv (research papers) and GitHub (open-source
activity), extracts every claim with a source and a confidence score, flags
contradictions instead of silently resolving them, and returns a structured
report where every material claim is cited.

---

## Architecture

> **In plain terms:** the question enters at the top. The Planner splits it up.
> Several Researchers work at once (not one after another; that is what keeps
> it fast). Their findings are turned into "claims + proof", checked, and
> cross-examined for contradictions. A Critic decides whether the research is
> good enough; if not, it loops back for more, but no more than 4 times. When
> it is good enough, the Synthesizer writes the report and the Citation Validator
> refuses to let it through unless every citation points at real stored
> evidence.

```
                              START
                                │
                                ▼
                          Planner Agent                (splits the question)
                                │
                                ▼
                        Task Decomposition             (into checked sub-questions)
                                │
                 ┌──────────────┼──────────────┐
                 ▼              ▼               ▼
             Researcher 1   Researcher 2    Researcher 3     ← all at the same time
             ┌───┼───┐                          (parallel, not sequential)
             ▼   ▼   ▼
           Web  SEC arXiv/GitHub
                 │
                 └──────────────┼──────────────┘
                                ▼
                        Evidence Extractor            (claim + the sentence that proves it)
                                │
                                ▼
                       Claim Normalization            (tidy each claim into a standard shape)
                                │
                                ▼
                        Verification Agent            (is this backed by multiple sources?)
                                │
                                ▼
                       Contradiction Check            (do any sources disagree?)
                                │
                          ┌─────┴─────┐
                          ▼           ▼
                       Missing     Sufficient
                          │           │
                          ▼           ▼
                       Re-plan     Synthesizer        (write the report)
                          │           │
                          └───────────┘
                    (loop, max 4 times)
                                      │
                                      ▼
                             Citation Validator       (every claim must have a real source)
                                      │
                                      ▼
                                Final Report
                                      │
                                      ▼
                             Citations + Sources
```

---

## Features

Each feature below has a one-line plain explanation.

- ✓ **Multi-agent research**: a team of specialised AI workers, not one model doing everything.
- ✓ **Agentic RAG**: the system looks things up in its own collected evidence before it writes, and decides what else to look up.
- ✓ **Web search**: finds pages on the open internet.
- ✓ **SEC EDGAR / arXiv / GitHub connectors**: pulls official company filings, scientific papers, and open-source project activity.
- ✓ **Hybrid retrieval**: finds relevant text both by exact keywords and by meaning, then re-sorts by true relevance.
- ✓ **Evidence graph**: a linked record of every claim, the exact quote that supports it, and the source it came from.
- ✓ **Citation verification**: the report cannot be published until every citation points at real stored evidence.
- ✓ **Contradiction detection**: when sources disagree, both values are recorded with a likely reason; nothing is silently chosen.
- ✓ **Durable execution**: the job saves its progress; a crash or restart resumes instead of starting over.
- ✓ **Streaming**: a live activity feed shows what the system is doing right now.
- ✓ **Model routing**: cheap fast models for simple steps, powerful models only for the hard reasoning; can also run fully local for comparison.
- ✓ **Evaluation**: a built-in scoreboard measures research quality; a failing score blocks a release.
- ✓ **LLM observability**: every AI call is traced with its time, token count, cost, and result.
- ✓ **Production deployment**: containerised, infrastructure-as-code, monitored, with a target of 99.5% uptime.

---

## Product modes

> **In plain terms:** sometimes you want a fast answer, sometimes a thorough
> report, and sometimes you want to keep digging into an answer you already got.

| Mode               | Target speed      | What it does                                                                                                        |
| ------------------ | ----------------- | ------------------------------------------------------------------------------------------------------------------- |
| **Quick**          | under ~30 seconds | Plan → search → rank → write a short cited answer                                                                   |
| **Deep**           | ~1-5 minutes      | The full pipeline above: parallel research, verification, contradiction checks, a critique loop, then a full report |
| **Conversational** | follow-ups        | "Go deeper on competitor X", reuses everything already gathered instead of starting over                            |

---

## Performance

> **In plain terms:** these are the goals for our own deployed demo, not promises
> about any outside service. The "Measured" column stays blank until we have run
> the real benchmark, we do not publish numbers we have not measured.

| Metric                                        | Target  | Measured      |
| --------------------------------------------- | ------- | ------------- |
| Quick research, P50 latency (typical time)    | ,       | `[benchmark]` |
| Quick research, P95 latency (slowest 1 in 20) | < 30 s  | `[benchmark]` |
| Deep research, P50 latency                    | ,       | `[benchmark]` |
| Deep research, P95 latency                    | < 180 s | `[benchmark]` |
| Cost per run                                  | ,       | `[benchmark]` |
| Tool failure rate                             | ,       | `[benchmark]` |
| Availability (uptime)                         | 99.5%   | `[benchmark]` |

---

## Evaluation

> **In plain terms:** we keep a fixed set of 100-300 test questions with known
> good answers and sources. After every change, the system answers all of them
> and is scored. If the score drops too low, the change is rejected
> automatically.

| Metric                              | What it means                                   | Threshold | Measured      |
| ----------------------------------- | ----------------------------------------------- | --------- | ------------- |
| Citation accuracy                   | cited sources actually support the claim        | ≥ 90%     | `[benchmark]` |
| Citation completeness               | important claims that have a citation           | ,         | `[benchmark]` |
| Retrieval recall                    | share of the right sources it managed to find   | ,         | `[benchmark]` |
| Claim correctness                   | claims that are factually right                 | ,         | `[benchmark]` |
| Groundedness / faithfulness         | report stays true to the evidence, no invention | ,         | `[benchmark]` |
| Recall@K / Precision@K / MRR / NDCG | standard search-quality scores                  | ,         | `[benchmark]` |
| Agent task success / recovery rate  | sub-tasks completed / failures recovered from   | ,         | `[benchmark]` |

---

## Infrastructure

> **In plain terms:** the user's browser talks to a "front counter" (the API).
> The front counter never makes you wait for the whole research job, it takes
> your request, hands you a ticket, and a separate "back room" worker does the
> long job. Postgres is the master filing cabinet, pgvector is the search-by-
> meaning index inside it, and S3 is the warehouse for bulky files like PDFs.
> Redis is the ticket queue and the bouncer that limits how much runs at once.

```
                              INTERNET
                                 │
                         ┌───────▼────────┐
                         │ Load Balancer  │   spreads traffic across servers
                         └───────┬────────┘
                                 │
                      ┌──────────▼──────────┐
                      │    Next.js App      │   the website you see
                      │      Frontend       │
                      └──────────┬──────────┘
                                 │  HTTPS / SSE (SSE = live progress feed)
                      ┌──────────▼──────────┐
                      │   API Gateway       │   the "front counter"
                      │     FastAPI         │
                      └──────────┬──────────┘
                                 │
                ┌────────────────┼────────────────┐
                ▼                ▼                ▼
           Auth Service     Research API      File API
          (log in / out)   (start/read runs) (uploaded PDFs)
                                 │
                           ┌─────▼─────┐
                           │  Redis    │   ticket queue + fast scratchpad + bouncer
                           └─────┬─────┘
                                 │
                          ┌──────▼──────┐
                          │ Task Worker │   the "back room" that does the long job
                          │  LangGraph  │
                          └──────┬──────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
          Planner           Researchers          Critic
                          ┌────┼────┐
                          ▼    ▼    ▼
                         Web  SEC  arXiv/GitHub
                                 │
                           Evidence Layer
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
          PostgreSQL        Vector DB          Object Store
        (master filing    (search-by-meaning  (S3: PDFs, saved
         cabinet)          index)              web pages, reports)
                                 │
                          Synthesis Agent
                                 │
                          Citation Validator
                                 │
                             Report → PostgreSQL
```

---

## Tech stack

> **In plain terms:** the "Why" column says what job each tool does. A
> non-technical reader can read only that column.

| Layer            | Choice                                                        | Why (plain English)                                                                                     |
| ---------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| Frontend         | Next.js, TypeScript, Tailwind, shadcn/ui, TanStack Query, SSE | Builds the website, its styling, and the live progress feed                                             |
| API              | FastAPI (modular monolith)                                    | The "front counter" that receives requests; one well-organised codebase, not a scatter of tiny services |
| Orchestration    | **LangGraph**                                                 | Runs the multi-step agent workflow, remembers progress, handles the loop, resumes after a crash         |
| Retrieval        | **LlamaIndex**                                                | Reads and indexes documents, then finds the relevant passages                                           |
| Model/tool layer | **LangChain**                                                 | A common adapter so we can swap AI providers and tools without rewriting agents                         |
| System of record | PostgreSQL                                                    | The master database, the single source of truth                                                         |
| Vectors          | pgvector (inside Postgres)                                    | "Search by meaning" lives in the same database, one less moving part                                    |
| Object storage   | S3                                                            | Cheap warehouse for big files (PDFs, saved pages, reports)                                              |
| Cache / queue    | Redis + Celery/ARQ                                            | The ticket queue for jobs and a fast cache to avoid repeat work                                         |
| Auth             | Auth.js + PostgreSQL                                          | Handles sign-up / login ourselves so we understand and control it                                       |
| Observability    | OpenTelemetry, Prometheus, Grafana, LangSmith                 | Tracing, metrics, dashboards, and AI-call inspection, so we can see what happened                       |
| Infra            | Docker, AWS, Terraform, GitHub Actions                        | Packaging, cloud hosting, "infrastructure written as code", and automated deploys                       |

---

## Frontend architecture

> Built in Phase 1, before any backend existed. The point of building it first
> is that the product decisions - what a run looks like, what a citation has to
> prove, how a contradiction is presented - get made against a real interface
> rather than being inferred from a schema later.

**Stack.** Next.js App Router, TypeScript in strict mode (plus
`noUncheckedIndexedAccess`), Tailwind v4 with semantic design tokens,
shadcn/ui-style primitives kept in-repo, TanStack Query, native `EventSource`.

**Server state is TanStack Query, and only TanStack Query.** There is no global
store. The SSE hook writes into the same cache the REST hooks read, so the live
feed and the fetched snapshot cannot disagree.

**One transport, two targets.** Components never call `fetch`; everything goes
through a typed client whose base URL is chosen by `NEXT_PUBLIC_API_MODE`. In
`mock` mode that URL is a set of Next.js route handlers implementing the
contract from `docs/TDD.md` section 18 - including a real `text/event-stream`
endpoint with `id:` frames and `Last-Event-ID` replay. Both sides import the
same DTOs from `packages/shared-types`, so a contract drift is a compile error.

**The activity checklist is derived, never transmitted.** `deriveStages()` folds
the event stream into the seven-stage checklist. A separate "current stage"
event could contradict the trace; a derivation cannot.

**Report Markdown is parsed, never injected.** Report text is synthesised from
untrusted web pages, so it is turned into React elements by a small parser that
supports exactly what the synthesizer emits. `dangerouslySetInnerHTML` appears
nowhere in the codebase, and a test asserts that HTML in source-derived text
renders as literal text.

**Unresolvable citations are visible.** A `[n]` with no matching citation
renders as a warning marker rather than disappearing - a broken evidence chain
is exactly the thing a reader must not be protected from.

**Not measured is not zero.** Every formatter renders `null` as an em dash. A
run with zero sources and a run whose sources were never counted are different
facts, and the evaluation dashboard depends on the distinction.

**Key paths**

```
apps/web/src/
├── app/(app)/              # signed-in pages: dashboard, research/*, evaluations, settings
├── app/api/mock/v1/        # mock backend: REST + SSE route handlers (deleted in Phase 2)
├── components/ui/          # shadcn-style primitives
├── components/research/    # run header, stage checklist, event feed, source/claim/
│                           # contradiction cards, report renderer, citation popover
├── lib/api/                # typed client, endpoints, query keys, hooks
├── lib/research/           # stage derivation, event labels, Markdown parser, form schema
├── lib/sse/                # EventSource hook + query-cache reconciliation
└── mocks/                  # deterministic fixture corpus, dataset builder, event timeline
```

---

## Backend architecture

> Built in Phase 2, against the contract the frontend already consumed. The
> ordering matters: the API had to satisfy a client that already existed, so
> "the frontend will adapt" was never available as an escape hatch.

**Stack.** Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic,
Redis, `uv` for locked dependencies. `ruff` and `mypy --strict` in CI.

**The API never runs a research workflow.** `POST /research` validates,
persists, publishes a started event and pushes a job. That boundary is an
explicit `JobQueue` interface rather than a function call, so it cannot later be
"optimised" into an inline await ([ADR 0001](docs/ADRs/0001-modular-monolith.md)).

**Authorisation is enforced before authentication exists.** Every research
object carries a `user_id`, every repository read is scoped by it, and a run
belonging to someone else returns the same `run_not_found` as one that never
existed - a 403 would confirm the id. Real sessions land in Phase 20; the
ownership rules are tested now rather than retrofitted onto a leaking surface.

**One error envelope.** Every failure - validation, framework 404, dependency
outage, unhandled exception - leaves as `ApiErrorBody`, carrying the request id
so a user-reported `trace_id` can be found in the logs. Internals never reach
the client; a test asserts a connection string in an exception message does not
appear in the response.

**Persistence is an interface.** `ResearchRepository` is a protocol owned by
the domain; the SQLAlchemy implementation lives in `app/db/repositories`. When
Phase 3 replaced the in-memory adapter with Postgres, not one endpoint changed.

**The database enforces what the API also validates.** 19 tables with foreign
keys, check constraints bounding every enum and every 0-1 score, `citext` so an
email cannot be registered twice by changing its case, `ON DELETE CASCADE` so
deleting research leaves no orphans, and `ON DELETE RESTRICT` on citations so a
cited claim cannot be deleted out from under a report. Validation in the
application is the fast path; the schema is the backstop that also covers a
migration, a backfill or a hand-written `UPDATE`.

**Every query is bounded and scoped.** Each one carries a `LIMIT` or addresses a
single row by key, and `user_id` is in the `WHERE` clause rather than applied in
Python afterwards. Listing uses keyset pagination on `(created_at DESC, id
DESC)` - matching the index - so a client paging through history cannot skip or
repeat a row when two runs share a timestamp.

**A session is a transaction, scoped to one request.** It commits when the
handler returns and rolls back when it raises. The one deliberate exception is
documented on the repository interface: a run is committed _before_ its job is
dispatched, so a queue outage leaves a recoverable `queued` row rather than
discarding what the user asked for.

**Not-built-yet is distinguishable from empty.** An endpoint whose phase has not
landed returns `501 not_implemented`, not a plausible-looking empty `200`.

**A cross-language contract test** reads `packages/shared-types` and asserts the
Python enums match the TypeScript ones. Nothing in either compiler can see the
other, so this is the seam that keeps them honest.

**Key paths**

```
apps/api/app/
├── api/            routing, DI, error handlers, SSE relay
│   └── v1/         health, auth, research, pending
├── core/           settings, logging, error taxonomy, enums, pagination
├── auth/           principal resolution and ownership
├── research/       schemas, repository boundary, event broker, service
├── db/             engine, session factory, ORM models, repositories, migrations
├── workers/        JobQueue interface, Redis and in-memory adapters
├── observability/  request-id and access-log middleware
└── agents/ retrieval/ sources/ evidence/ reports/ evaluations/
                    module boundaries, filled by later phases
```

---

## Repository structure

```
aether-research/
├── apps/
│   ├── web/                 # Next.js frontend (the website)
│   └── api/                 # FastAPI + worker (front counter + back room)
│       └── app/
│           ├── api/  agents/  research/  retrieval/  sources/
│           ├── evidence/  models/  db/  workers/  observability/
├── packages/
│   ├── shared-types/        # shared data definitions used by both website and server
│   ├── prompts/             # versioned instructions given to the AI models
│   └── evaluation/          # the scoring harness
├── data/
│   ├── seed/  eval/  fixtures/     # eval/ holds the 100–300 test questions
├── infra/
│   ├── docker/  terraform/  kubernetes/  monitoring/
├── docs/
│   ├── PRD.md  TDD.md  architecture.md  threat-model.md  evaluation.md
│   └── ADRs/               # short records of "why we chose X"
├── scripts/
├── .github/workflows/      # automated test + deploy pipelines
├── docker-compose.yml
├── Makefile
└── README.md
```

---

## Current status

**Phases 0-3 are complete.** The repository, documentation and local
infrastructure exist; the frontend is a working product; it talks to a real
FastAPI backend; and that backend persists to PostgreSQL. Switching the
frontend between fixtures and the live API is one environment variable, with no
code change ([ADR 0009](docs/ADRs/0009-frontend-mock-transport.md)).

| Phase | Scope                                                                                                        | Status  |
| ----- | ------------------------------------------------------------------------------------------------------------ | ------- |
| 0     | Monorepo, tooling, local stack, ADRs, architecture/threat-model/evaluation docs                              | Done    |
| 1     | Frontend product prototype against a mock API, unit + end-to-end tests                                       | Done    |
| 2     | Backend foundation: FastAPI, typed settings, error contract, authorisation, health probes, research API, SSE | Done    |
| 3     | Data layer: 19-table PostgreSQL schema, Alembic migrations, pgvector, repositories, pooling                  | Done    |
| 4     | Object storage: S3-compatible abstraction, MinIO locally                                                     | Next    |
| 5+    | Model gateway, research tools, RAG, LangGraph agents, evaluation, observability, load testing, deployment    | Planned |

In **mock mode** the whole product is explorable: browse research history, start
a run, watch the agent timeline stream over SSE, inspect sources and duplicate
clusters, read claims with their verbatim evidence spans, see contradictions
recorded rather than resolved, and open a report where every `[n]` resolves to a
source and the quote behind it. Every figure there is synthetic fixture data,
and the app says so in a banner on every page.

In **live mode** the same UI runs against the real stack: runs are validated,
authorised, written to Postgres and queued, `POST /research` returns `202`, the
SSE stream is real, and a run survives a restart of the API process.

**There is no worker yet**, so a created run stays `queued`. The tables for
sources, evidence, reports and traces exist and are fully constrained, but
nothing writes to them until Phases 6-12, and the endpoints return empty
collections rather than inventing content. Capabilities whose phase has not
landed return `501 not_implemented`, so "not built yet" is always
distinguishable from "no results".

No benchmark has been executed, and the evaluations page says so.

---

## Quick start

Requires Node 22+. The backend additionally needs Python 3.12+ and uv.

```bash
# 1. clone and configure
git clone <repo-url> "aether-research" && cd aether-research
cp .env.example .env

# 2. install workspace dependencies
#    --legacy-peer-deps is needed only for a cold resolve with no lockfile
#    (npm arborist bug in the vitest peer graph); `npm ci` does not need it.
npm install --legacy-peer-deps

# 3. run the frontend against the mock API
make dev                      # or: npm run dev
open http://localhost:3000
```

### Verifying it

```bash
make ci          # frontend + backend: format, lint, typecheck, unit tests
make test-e2e    # Playwright smoke suite (builds and serves the app)
```

The unit suite covers the pure logic that the UI depends on - event-to-stage
derivation, the report Markdown parser, form validation, the HTTP error
contract, and the self-consistency of the fixtures (every citation must resolve
to a claim, an evidence span and a source). The end-to-end suite drives the real
browser through the whole journey, including a live `text/event-stream`.

### Running the backend

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
make up            # Postgres + pgvector, Redis, MinIO, Prometheus, Grafana
make api-install   # create apps/api/.venv from the lockfile
make api           # uvicorn on :8000, OpenAPI UI at /docs
```

Point the frontend at it. This is the only change required:

```bash
NEXT_PUBLIC_API_MODE=live
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1
```

Outside the test environment the API requires Redis and reports itself unready
without it. That is deliberate: silently dropping research jobs is worse than
failing loudly.

---

## Build roadmap

> **In plain terms:** build it in slices. Each row adds one capability on top of
> the last. Nothing after row 0 is attempted until row 0 works.

| Phase | Scope                                                                                         | Status |
| ----- | --------------------------------------------------------------------------------------------- | ------ |
| 0     | Product prototype, website with fake data, so the experience is real before the engine exists | ☑      |
| 1     | Basic backend, accounts, database, create/read research                                       | ☐      |
| 2     | First AI, one Planner + Researcher + Synthesizer, single straight-line path                   | ☐      |
| 3     | Web research, real searching, fetching, parsing, and citations                                | ☐      |
| 4     | RAG, indexing and smart retrieval over collected documents                                    | ☐      |
| 5     | Multi-agent, add Critic + Verifier, run researchers in parallel                               | ☐      |
| 6     | Durable execution, save-points, queue, background workers, resume                             | ☐      |
| 7     | Evaluation, the test set, the scoreboard, the release gate                                    | ☐      |
| 8     | Production engineering, monitoring, rate limits, caching, load tests, security                | ☐      |
| 9     | Deployment, cloud hosting, infrastructure-as-code, automated deploys, monitoring              | ☐      |

---

## Documentation

- [`docs/PRD.md`](docs/PRD.md), Product Requirements Document (what we are building and why; written for everyone)
- [`docs/TDD.md`](docs/TDD.md), Technical Design Document (how it is built; plain-English intro on every section)
- `docs/architecture.md`, architecture deep-dive
- `docs/threat-model.md`, security threat model
- `docs/evaluation.md`, evaluation methodology
- `docs/ADRs/`, architecture decision records

---

## Glossary

| Term                             | Plain meaning                                                                                                                  |
| -------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| **Agent**                        | A single AI worker with one job (e.g. the Planner). A "multi-agent" system is a team of them.                                  |
| **LLM**                          | Large Language Model, the AI that reads and writes text (e.g. GPT, Claude).                                                    |
| **RAG**                          | Retrieval-Augmented Generation, "look it up in real sources, then answer", instead of answering from memory.                   |
| **Orchestration**                | Coordinating the many steps and workers in the right order, with loops and retries.                                            |
| **API**                          | The way two pieces of software talk to each other; here also the "front counter" that receives requests.                       |
| **Frontend / Backend**           | Frontend = what you see in the browser. Backend = the servers and databases behind it.                                         |
| **Queue / Worker**               | You drop off a request and get a ticket (queue); a separate program (worker) does the slow job in the background.              |
| **SSE (streaming)**              | Server-Sent Events, a live one-way feed from server to browser, like a delivery tracker.                                       |
| **Checkpoint**                   | A saved snapshot of progress, like a save point in a video game, so work can resume after a crash.                             |
| **Database / Postgres**          | The master filing cabinet where the authoritative records live.                                                                |
| **Vector search / pgvector**     | "Search by meaning", finds related text even when the words differ.                                                            |
| **BM25 / full-text search**      | The classic "search by exact keywords" method.                                                                                 |
| **Hybrid retrieval**             | Using keyword search and meaning search together, then re-ranking the combined results.                                        |
| **Reranking**                    | A second, smarter pass that re-sorts search results by how relevant they really are.                                           |
| **Embedding**                    | A list of numbers that represents the meaning of a piece of text, so a computer can compare meanings.                          |
| **Knowledge graph**              | A map of things (companies, people, products) and how they relate.                                                             |
| **Object storage / S3**          | A cheap warehouse for large files, separate from the database.                                                                 |
| **Redis**                        | A very fast in-memory store used here as the job queue, cache, and rate-limiter.                                               |
| **Rate limiting**                | "Take a number", capping how many requests a user or the system handles at once.                                               |
| **Backpressure**                 | When the system is busy, new work waits in line instead of overwhelming it.                                                    |
| **Concurrency**                  | How many things run at the same time.                                                                                          |
| **Idempotent**                   | Safe to repeat, doing the same step twice has the same effect as doing it once.                                                |
| **Prompt injection**             | A trick where text on a web page tries to give the AI new instructions; we treat all fetched text as data, never instructions. |
| **SSRF**                         | Server-Side Request Forgery, tricking our server into fetching a private internal address; blocked by design.                  |
| **Observability**                | Being able to see what the system did, traces, metrics, dashboards, logs.                                                      |
| **P50 / P95 / P99 latency**      | The typical time / the slowest 1-in-20 / the slowest 1-in-100.                                                                 |
| **CI/CD**                        | Automated pipelines that test every change and deploy the good ones.                                                           |
| **IaC (Infrastructure as Code)** | Servers and cloud setup defined in text files so they are repeatable and reviewable.                                           |
| **Container / Docker**           | A standard box that holds an app plus everything it needs to run, identically everywhere.                                      |
| **Modular monolith**             | One codebase kept in clean sections, simpler than many tiny services, but still organised.                                     |
| **Confidence score**             | A 0-1 number saying how sure the system is about a claim.                                                                      |
| **Citation**                     | The `[n]` marker in the report that links a statement to its source and exact supporting quote.                                |

## License

See [`LICENSE`](LICENSE).
