# Aether Research — working context

Read this first every session. It is the map; the details live in the documents
it points at.

## What this is

An autonomous multi-agent research platform. A user asks a complex question; the
system decomposes it, researches the parts in parallel, extracts evidence with
verbatim spans, detects contradictions, loops under a critic until coverage is
sufficient or a hard limit is hit, then writes a report where every factual
claim carries a validated citation.

**It is not a chatbot that searches the web.** The engineering value is
everything around the model call: durability, bounded loops, cost governance,
untrusted-content handling, measured evaluation, observability, deployment.

## Current state

**Phases 0–19 of 25 are complete.** Full plan and per-phase status:
[`docs/PHASES.md`](docs/PHASES.md) — read it before starting new work.

| Layer                              | State                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Frontend (`apps/web`)              | Complete product surface, 100 unit + 19 e2e tests. Runs against mock fixtures or the live API by one env var.                                                                                                                                                                                                                                                                                                                                          |
| API (`apps/api`)                   | FastAPI: research surface, file uploads, SSE, authorisation, error contract, health probes, `/metrics`, `/evaluations`. 1225 tests.                                                                                                                                                                                                                                                                                                                    |
| Database                           | PostgreSQL, 22 tables (the trace and evaluation tables are written for the first time in Phases 16 and 18), Alembic migrations on two branches (`core`, `vector`), pgvector column sized for the declared embedding model (768). A run's row carries the worker's lease (ADR 0017), and `research_events` the progress stream (ADR 0018); UUIDs come back as `uuid.UUID`, never the driver's subclass.                                                 |
| Object storage                     | `ObjectStorage` over S3/MinIO plus a filesystem backend. Bounded, classified, readiness-probed. Written by uploads and by ingestion.                                                                                                                                                                                                                                                                                                                   |
| Model gateway                      | `LLMGateway` over Anthropic, OpenAI and Ollama. Registry, role/mode routing, retry, failover, call ledger. Every agent reaches a model through it and prices its own calls with `cost_of`. Embeddings are cached per text; completions deliberately are not (ADR 0019).                                                                                                                                                                                |
| Research tools                     | Six tools behind a `Toolbelt`: search, fetch, parse, SEC, arXiv, GitHub. Four-layer SSRF guard, untrusted-content type, per-call ledger. Search, fetch and parse are cached by content hash and coalesced in flight. Called by the web and data researchers; the synthesizer and validator are given no belt at all.                                                                                                                                   |
| Ingestion (`app/retrieval`)        | Upload API; PDF, HTML, Markdown and text parsed in a killable, scrubbed child process; offset-exact LlamaIndex chunking; gateway embeddings; chunk metadata filters. Called by the web and data researchers, which is what turns a fetched page into a citable source. The worker ingests a run's attached uploads (Phase 13).                                                                                                                         |
| Retrieval (`app/retrieval`)        | `PostgresRetriever` behind the `Retriever` Protocol: pgvector cosine and OR-ed Postgres full-text run concurrently, fused by reciprocal rank, reranked for diversity by MMR. Metrics and a benchmark with measured numbers. Called by the document researcher and by evidence extraction.                                                                                                                                                              |
| Research graph (`app/agents`)      | LangGraph `StateGraph` over a typed, checkpointed `ResearchState`: parallel researchers via `Send`; every node wrapped with cancellation, the FR-8 ceilings, a timeout and usage accounting; Postgres checkpoints whose tables Alembic owns, read back through a derived allowlist. Every node is implemented.                                                                                                                                         |
| Agents (`app/agents`)              | All nine: planner, three researchers behind a router, evidence, claim normalization, verification, contradictions, critic, synthesis, and a citation validator that calls no model. Versioned prompts as package data; a model cites by catalogue number and never emits an identifier (ADR 0015).                                                                                                                                                     |
| Evidence (`app/evidence`)          | The chain made durable: a projection of graph state onto claims, evidence spans and contradictions, idempotent on derived ids; source deduplication into clusters, so corroboration counts distinct content rather than copies; origin credibility from a declared table. `/sources` and `/evidence` serve real rows.                                                                                                                                  |
| Reports (`app/reports`)            | A validated draft assembled into the report a reader opens: claim numbers renumbered to citation ordinals, Evidence and References built from rows rather than written, citations whose foreign keys the database enforces. `/report` serves it with the citation check's verdict.                                                                                                                                                                     |
| Worker                             | `python -m app.workers.runner`: claims a run against its row, ingests its attachments, streams the graph while renewing the lease and writing the run's phase, progress and counts, then records the outcome. Retries with backoff, hands runs back on shutdown, reconciles what the queue lost. Narrates the run as it goes, writes the trace and the call ledger, holds the run to its cost ceiling, and exposes Prometheus metrics on its own port. |
| Streaming (`app/research`)         | The events the worker emits, carried across processes: `eventbus.py` is Redis pub/sub with a capped buffer over a durable log; `events.py` is the vocabulary and the in-process transport. `publish` numbers an event by storing it, so two emitters can never share an `id:` (ADR 0018).                                                                                                                                                              |
| Caching (`app/cache`)              | Search answers, fetched pages, extracted articles and embeddings, keyed by content hash and coalesced in flight. A closed namespace list is how "never cache per-user data globally" is kept structurally. Redis in a deployment, bounded in-memory under `APP_ENV=test` (ADR 0019).                                                                                                                                                                   |
| Ledger (`app/observability`)       | Every node execution is an `agent_runs` row, with the tool and model calls it made hanging from it; the current span travels in a ContextVar so a belt shared by the process can still say which step called it. `/activity` serves real rows (ADR 0020).                                                                                                                                                                                              |
| Cost governance                    | A run's ceiling is enforced _before_ each call, not only between nodes: a refusal ends discovery and the run still writes its report. An unpriced model is refused under a budget. Spend is counted from the ledger, never from a second tally.                                                                                                                                                                                                        |
| Telemetry (`app/observability`)    | Prometheus metrics on both processes, OpenTelemetry spans at every seam, `trace_id`/`span_id` on the trace rows, a Grafana dashboard in `infra/monitoring`, LangSmith off unless asked. `/evaluations/system` is measured from rows; every field is nullable and null means _not measured_.                                                                                                                                                            |
| Evaluation (`app/evaluations`)     | A versioned dataset in `data/eval/cases`, structural scorers, configurable gates stored with each result, `make evaluate`, and `/evaluations`. **Nothing has been executed**: running it needs credentials and spends money per case.                                                                                                                                                                                                                  |
| Scenario tests (`tests/scenarios`) | The fifteen situations Phase 19 requires the system to survive, each driven through the whole vertical slice: queue, worker, lease, graph, nine real agents, the real toolbelt over a scripted socket, ingestion, retrieval, the projections and real Postgres. Only the model and the socket are replaced. `catalogue.py` holds the list as code and fails the build when a scenario loses its test.                                                  |
| Everything else                    | Not built. Endpoints for unbuilt capabilities return `501 not_implemented`.                                                                                                                                                                                                                                                                                                                                                                            |

Nothing fabricates data to fill a gap. The only benchmark executed so far is
the retrieval benchmark (Phase 8, lexical arm only); **no end-to-end
evaluation has run**, and no live model call has been made on this machine -
every run executed so far has been over scripted agents. The evaluation suite
exists and will produce numbers the moment someone runs it with credentials;
until then every gate is ungated and every unmeasured metric reads as _not
measured_ rather than as zero.

**Licensing.** Proprietary, all rights reserved - see [`LICENSE`](LICENSE). The
repository is public on GitHub for reading, not reuse. Never add an open-source
license or `"license": "MIT"` to a manifest; `apps/api/tests/test_licensing.py`
fails the build if one appears. Code copied in from open-source projects must be
listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

## Specifications (authoritative)

| Document                                       | Contents                                                                |
| ---------------------------------------------- | ----------------------------------------------------------------------- |
| [`docs/PHASES.md`](docs/PHASES.md)             | The 25-phase build plan and what is done                                |
| [`docs/PRD.md`](docs/PRD.md)                   | Product requirements, FR-1..FR-10, three product modes                  |
| [`docs/TDD.md`](docs/TDD.md)                   | Technical design: components, agent graph, §7.2 schema, §18 API surface |
| [`docs/architecture.md`](docs/architecture.md) | System map                                                              |
| [`docs/threat-model.md`](docs/threat-model.md) | STRIDE per trust boundary; prompt injection and SSRF                    |
| [`docs/evaluation.md`](docs/evaluation.md)     | Metrics, thresholds, the no-fabricated-numbers rule                     |
| [`docs/ADRs/`](docs/ADRs/)                     | 20 accepted decisions. New irreversible choice ⇒ new ADR.               |

## Repository structure

```
apps/web/          Next.js frontend
  src/app/         pages: (app)/dashboard, research/*, evaluations, settings; login
  src/app/api/mock/v1/   mock backend (REST + SSE) — deleted when live mode is the only mode
  src/components/  ui/ (shadcn-style), research/, layout/, common/, evaluations/
  src/lib/         api/ (client, hooks), research/ (stages, markdown, schema), sse/
  src/mocks/       deterministic fixture corpus
  e2e/             Playwright
apps/api/          FastAPI + worker, one codebase two process types (ADR 0001)
  app/api/         routing, DI, error handlers, SSE relay
  app/core/        settings, logging, errors, enums, pagination
  app/research/    run lifecycle, repository protocol, service, recorder.py
                   (a finished run's evidence and report in one transaction -
                   the citations' foreign keys require that order), and the
                   progress bus: events.py (vocabulary, EventDraft, the
                   in-process transport) and eventbus.py (Redis fan-out, the
                   durable broker, build_event_broker)
  app/db/          base, models/, repositories/
  app/workers/     the second process type: queue.py (JobQueue, Redis and
                   in-memory adapters), lifecycle.py (the lease, as the
                   transitions a worker performs), progress.py (a node -> the
                   run's status and its bar), events.py (a node -> what the
                   stream says), worker.py (one run), loop.py (reserve, sweep,
                   drain), runner.py (the process). No write here can overwrite
                   a status this worker does not hold, and nothing announced is
                   announced twice.
  app/cache/       the response cache: backend.py (Redis, bounded in-memory,
                   null), keys.py (the closed namespace list and content
                   hashing), single_flight.py, store.py (TTLs, ceilings, and
                   read-through). Domain encodings live with their types -
                   app/sources/caching.py
  app/storage/     ObjectStorage protocol, S3 + filesystem backends, key namespace
  app/models/      LLM gateway, registry.yaml, routing, provider adapters
                   (NOT the ORM — that is app/db/models/)
  app/sources/     the only module that talks to the open internet: SSRF
                   guard, guarded HTTP client, untrusted-content type,
                   sanitiser, tools/ (search, fetch, extract, sec, arxiv,
                   github), toolbelt
  app/retrieval/   ingestion (formats, parsers, the isolated parser child
                   parse_worker, chunking, embedding, the pipeline, uploads)
                   and retrieval (filters, retriever, fusion, rerank, metrics,
                   benchmark). Phase 8's Retriever is the seam ADR 0003 named.
  app/agents/      the research graph (state, node contracts, loop control,
                   checkpointer, runner) and the nine agents that fill it:
                   planner, researchers/ (web, documents, data, router),
                   extraction, verification, critic, synthesis, citations.
                   prompts/ holds the versioned templates as package data;
                   catalog.py numbers state for a model, outputs.py is what a
                   model may return, factory.py wires them together.
  app/evidence/    the evidence chain as rows: dedup.py clusters a run's
                   sources, projection.py writes the graph's claims, spans and
                   contradictions onto them, repository.py is the store's
                   protocol. Never resolves a contradiction, and never
                   un-resolves one a person resolved.
  app/reports/     assembly.py turns a validated draft into the report a reader
                   opens - markers renumbered to citation ordinals, Evidence and
                   References assembled from rows; projection.py writes it.
  app/evaluations/ the benchmark suite: dataset.py (cases and versions),
                   metrics.py (pure structural scorers), thresholds.py (the
                   gate, ungated until a baseline exists), runner.py,
                   report.py, __main__.py (`make evaluate`)
  app/observability/
                   ledger.py (a span per node execution, the ContextVar the
                   recorders read), metrics.py (the Prometheus instruments),
                   instruments.py (the metered recorders), tracing.py (OTel
                   setup, the LangSmith client), retrieval.py, system.py,
                   middleware.py
  migrations/      Alembic, two branches: core (relational) and vector
                   (needs pgvector). `alembic upgrade heads` applies both.
  tests/           pytest; tests/support/postgres.py provisions a real database.
                   tests/scenarios/ is Phase 19: catalogue.py (the fifteen
                   scenarios as code, and the decorator that claims one),
                   world.py (the vertical slice - a scripted model behind the
                   real gateway, a scripted socket behind the real guarded
                   client), story.py (the ordinary run each one departs from)
packages/shared-types/   TypeScript DTOs shared by web and mock API
packages/prompts|evaluation/   prompts/ is a pointer (ADR 0015); evaluation/ is Phase 18
data/seed|fixtures|eval/       eval/ holds the benchmark dataset (Phase 18)
infra/docker|terraform|kubernetes|monitoring/
```

## Tech stack (as pinned)

**Frontend** — Next.js 16, React 19, TypeScript 5.9 strict (+`noUncheckedIndexedAccess`),
Tailwind v4, shadcn-style components in-repo, TanStack Query 5, native `EventSource`,
Zod 4. Vitest 4, Playwright 1.63.

**Backend** — Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic,
asyncpg, pgvector, Redis, aioboto3 (S3), anthropic + openai SDKs, httpx2,
trafilatura (readability), defusedxml (untrusted XML), llama-index-core
(chunking only, imported lazily), pypdf, py3langid (language detection), LangGraph with its Postgres checkpointer
(psycopg 3, in its own pool).
`uv` for locked deps. `ruff` + `mypy --strict`. pytest, with `moto` in server
mode for a real S3 endpoint and `httpx2.MockTransport` for the model providers.

OpenTelemetry (API + SDK + OTLP/HTTP exporter) and `prometheus-client`. The
OTel API is a hard dependency because spans are opened unconditionally against
a no-op provider; the SDK and exporter load only when an endpoint is
configured.

**Planned** — AWS ECS Fargate via Terraform (ADR 0008).

## Commands

```bash
npm install --legacy-peer-deps   # cold resolve only; npm ci afterwards
make dev            # frontend on :3000 against mock fixtures
make up             # Postgres+pgvector, Redis, MinIO, Prometheus, Grafana
make api-install && make migrate && make api    # backend on :8000
make ci             # format, lint, typecheck, unit tests, both stacks
make test-e2e       # Playwright
make migrate-check  # migration upgrade/downgrade round trip
make worker         # the research worker (needs Postgres and Redis)
make migration m="add x"   # autogenerate on the core line; HEAD=vector@head for pgvector
make test-scenarios        # the fifteen end-to-end research scenarios (part of api-test)
make benchmark-retrieval   # measure the retrieval strategies and the chunk size
make evaluate              # run the evaluation dataset (real runs; costs money)
```

Point the frontend at the real API with `NEXT_PUBLIC_API_MODE=live` and
`NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1`. Nothing else changes.

## Environment constraints on this machine

Hard-won; do not rediscover them.

- **Docker is not installed, and neither is Redis.** `make up` cannot run here.
  The API and the worker both need Redis outside `APP_ENV=test`, so use
  `APP_ENV=test` for local end-to-end checks - it selects the in-memory queue.
  `tests/test_worker_queue.py` skips its Redis tests with a reason when no
  server answers; CI runs them against a `redis:8-alpine` service container.
- **pgvector is not installed** in the local PostgreSQL 17, and no
  pip-installable build exists for Python 3.13 on Windows. The suite migrates
  the relational line (`core@head`); the vector line (0002, 0004) and the vector
  tests skip locally with an explicit reason, and CI covers them with the
  `pgvector/pgvector:pg17` service container.
- **The test suite provisions its own Postgres**: a throwaway cluster in a temp
  directory on a random port with trust auth, destroyed afterwards. It touches
  no existing cluster and needs no credentials. `AETHER_TEST_DATABASE_URL`
  overrides it.
- **The storage tests start their own S3 server** (`moto` in server mode, on a
  free port) rather than patching botocore, so signing and HTTP really run. No
  MinIO or Docker needed.
- **`APP_ENV=test` selects the filesystem storage backend**, so the object-store
  path is runnable here. It is refused in production.
- **`OPENAI_API_KEY` and `ANTHROPIC_BASE_URL` are set in this machine's shell.**
  So the OpenAI provider builds even with no `.env`, and the Anthropic SDK will
  honour that base URL. Both are documented SDK behaviour, not a bug — but it
  explains a provider list that looks larger than the configuration suggests.
- **Both vendor SDKs are built on `httpx2`, not `httpx`.** `httpx` is a test-only
  dependency (the ASGI client); runtime HTTP is `httpx2` everywhere.
- **A few tests make real DNS and HTTP calls.** The SSRF guard's allow-path test
  resolves a real hostname (it skips cleanly with no DNS). Everything else uses
  `httpx2.MockTransport`.
- **The repo lives under OneDrive, and its path contains a space.** Desktop is
  redirected into OneDrive, so all 77k files - `.venv` (31k) and `node_modules`
  (42k) among them - are tracked by the sync engine, which has burned over seven
  hours of CPU. Moving the repo to `C:/dev/aether-research` was tried, measured
  and reverted by choice; if it is ever moved again, two repairs follow, and
  neither is optional: `npm ci`, because npm's workspace links are absolute
  junctions and every `@aether/*` import fails silently at collection time
  without it, and `uv sync`, because the editable install records a path. The
  space in the path is why Vitest's `forks` pool cannot hand off to workers, so
  the config pins `pool: 'threads'`.
- **Vitest needs its worker count capped, and the config does it.** Vitest
  defaults to one worker per core; that many jsdom environments starting at once
  on this CPU makes every worker miss its startup handshake, and the run reports
  `no tests` rather than a failure. `maxWorkers: 4` is set in
  `apps/web/vitest.config.mts`; `npm run test` is green in about 40 seconds.
- **Playwright is slower in parallel on this machine, and flaky with it.** The
  config leaves the local worker count to Playwright (`workers: 1` only under
  CI), and the full suite measured 3.1 minutes with one contention failure
  against 1.4 minutes and 19/19 at `--workers=1`. Two of the journeys drive a
  live mock run each, and concurrently they miss the 15-second `expect`
  timeout on the stream indicator - which looks like a broken stream and is
  not. Run `npx playwright test --workers=1` here; CI already does.
- **npm cold resolve crashes** (arborist bug in the vitest peer graph) without
  `--legacy-peer-deps`. `npm ci` from the committed lockfile is fine.
- **The API suite runs in parallel and takes about eleven minutes.** `make
api-test` uses four xdist workers; `WORKERS=0` makes it serial, which is what
  to do when a failure needs reading. Every worker provisions a database of its
  own - a whole cluster locally, a separate database on the server when
  `AETHER_TEST_DATABASE_URL` is set - because the suite truncates every table
  between tests and workers sharing one would delete each other's rows. It was
  over twenty minutes serially, which no single command could finish here; still
  prefer running only the modules a change touches.
- **`Path.resolve()` on Windows can return the extended-length form.** When a
  path is resolved while its own parent is being created by another task,
  `resolve()` hands back `\\?\C:\...` rather than `C:\...`. Any containment
  check comparing that against a plain-form root refuses a path that is plainly
  inside it - which is how the object store silently dropped one upload in seven
  whenever a researcher collected pages concurrently (Phase 19). Normalise both
  sides with `app.storage.filesystem._plain` before comparing paths.
- **A scenario that times out reports itself as a run that found nothing.** The
  graph gives every node a ceiling, and a real researcher node - search, fetch,
  parse, chunk, store - exceeds a small one on this machine, especially four-wide
  under xdist. The run then has no sources, so no evidence, so no claims, and
  fails at synthesis: three layers from the cause, with nothing in the assertion
  naming a timeout. `tests/scenarios/world.py` therefore sets
  `graph_node_timeout_seconds` to 90 and the serve deadline to 180, and
  `tests/support/worker.py` holds `serve_until` at 60. All three are hang
  detectors, not measurements - if a scenario is slow, that is this CPU, and
  tightening them only turns load into a red suite.
- **The heavy third-party imports are deliberately lazy; keep them that way.**
  The Anthropic SDK costs about 10 s to import, OpenAI 6.5 s and aioboto3 2.6 s,
  all of it CPU spent building Pydantic models rather than reading files. Each is
  imported inside the function that needs it, so `import app.main` is about 6 s
  rather than 28, and a test process imports none of them. Moving any of these
  back to module scope puts 20 s on every pytest run and every worker start. The
  test settings pin the provider credentials to `None` for the same reason, and
  because a suite whose provider list depends on the developer's shell is not
  hermetic.
- **A pytest invocation still costs about forty-five seconds before it runs a
  test**, so the number of invocations matters more than the number of tests.
  Collection is 35 s and a throwaway cluster 8.4 s plus 3 s of migrations.
  `uv run` adds a further 2.8 s per command over calling
  `.venv/Scripts/python.exe` directly.
- **This machine's CPU is downclocked to about a third of its capability.**
  `% Processor Performance` reads 68-71 against a nominal 2.1 GHz on an i5-13420H
  that boosts to 4.6, and it never turbos even under load; a pure-Python loop
  runs 3-4x slower than the chip should manage. Everything here is
  single-threaded CPU-bound Python, so estimate accordingly - and if timings ever
  improve sharply, the power mode was changed rather than the code.
- **A killed pytest run used to leak its Postgres cluster**, and the harness now
  clears up after itself: each cluster records the pid that created it, and every
  run stops and removes the ones whose process is gone. `pg_ctl` starts a
  _detached_ postmaster, so killing pytest leaves a running server - which is why
  the test is whether the owner is alive, not whether the cluster is. Twenty of
  them and 1.4 GB accumulated in one session before this existed. If one ever
  survives anyway, sweep with
  `Remove-Item "$env:TEMP\aether-pg-*" -Recurse -Force`, and never kill a
  `postgres.exe` without checking its `-D` first: the real PostgreSQL 17 service
  is running on 5432 and looks the same in a process list.
- **Windows Defender exclusions are the user's to set, not the agent's.** They
  are security settings and need an elevated shell; recommended entries are the
  repository root and `%TEMP%\aether-pg-*`.
- Bash heredocs fail above roughly 8 KB — use the Write tool for larger files.
- **Ollama is installed but not running**, and has pulled neither the registry's
  chat model nor `nomic-embed-text`. Live embeddings are unverified here; tests
  drive the real adapter over `httpx2.MockTransport`.
- **LlamaIndex takes about 4.5 s to import cold.** It is imported lazily inside
  the chunker, so the API process never pays it.
- **`.env.example` is committed with CRLF line endings.** An editor that
  normalises them turns a small change into a whole-file rewrite; check
  `git diff --stat --ignore-cr-at-eol` before committing. This shell's `grep -c
$'
'` and `cat -A` both report the file as LF, which is wrong - read it in
  Python with `newline=""` to see what is really there.
- **The agent's file tool decodes backslash-u escape sequences** in content it
  writes into the characters they name (one became a NUL byte in a comment).
  Build non-ASCII characters with `chr()`, or type the character itself.
- **The repo stores LF and there is no `.gitattributes`.** Python's
  `Path.write_text` emits CRLF here, which turns a three-line edit into a
  whole-file diff. Write with `newline="
"`, or check `git diff --stat`
  before committing.
- **psycopg's async mode refuses Windows' default (proactor) event loop.**
  LangGraph's Postgres checkpointer runs on psycopg, so the tests that open it
  live in `apps/api/tests/checkpointer/`, whose conftest gives them a selector
  loop. It is scoped by directory, not by marker: once a pytest-asyncio
  loop-factory hook exists it must return a mapping for every test it sees. A
  worker run locally on Windows needs
  `asyncio.run(..., loop_factory=asyncio.SelectorEventLoop)`.
- **LangGraph's Postgres checkpointer stores `str`, `int`, `float` and `bool`
  values inline as JSON**, subclasses included, so a `StrEnum` at the top of the
  graph state comes back as a plain string. Keep enums inside models;
  `tests/test_graph_state.py` fails on a top-level field that breaks this.
- **The same trap, from the other end: asyncpg returns `pgproto.UUID`,** a
  subclass of `uuid.UUID` that passes every isinstance check and every Pydantic
  field - and that LangGraph's serializer then refuses to reconstruct, because
  its allowlist is derived from the state's declared types. A resumed run fails
  far from the cause. `NormalisedUUID` in `app/db/base.py` converts on the way
  out, so define UUID columns with `uuid_pk()` or `fk_uuid()` and never with a
  bare `PgUUID`. `tests/test_schema.py` fails if the conversion is removed.
- **`langgraph` depends on `langgraph-sdk`, which pins `websockets` below 17**,
  so the lockfile holds 16.x. uvicorn needs 13 or later.
- **Alembic's `fileConfig` disables every existing logger** unless told not to,
  and `migrations/env.py` imports the application to reach its metadata - so
  running migrations in-process used to silence every `app.*` logger while the
  migration output kept flowing. Fixed with `disable_existing_loggers=False`;
  `test_errors_and_logging.py` fails if it comes back. Any test that asserts on a
  log line depends on this.
- **The suite pins `log_level` to `warning`, and a disabled logger never builds
  a record** - so a log call that would raise cannot. `logging.makeRecord`
  refuses an `extra` field named after a `LogRecord` attribute, and this has now
  hidden two defects that way: Phase 10's `filename` in an error context, and
  Phase 19's three `created` fields, one of which made every ingestion throw at
  the default level. `tests/scenarios/conftest.py` turns the `app` loggers up to
  INFO for the scenario suite, and `test_errors_and_logging.py` scans `app/` for
  the whole class. Never add an `extra={...}` key without checking it against
  `_RESERVED_ATTRS`.
- **LangSmith caches environment reads** (`langsmith.utils.get_env_var` is an
  `lru_cache`). A test that sets `LANGSMITH_TRACING` must clear the cache before
  and after, or every later test in the process sees tracing on.
- **`npm run format:check` covers Markdown and YAML across the repo**, backend
  docs included. Run it after editing docs; Phase 8 shipped five files that
  failed it.
- **An Alembic revision id may not exceed 32 characters.** `alembic_version.version_num`
  is `varchar(32)`, so a longer id fails on the `UPDATE alembic_version` that ends
  the upgrade - every database-provisioning test at once, and a long way from the
  cause (Phase 12).

## Engineering rules (non-negotiable)

From the original brief. These override convenience.

**Never**

- put a secret in frontend code, or read one outside the typed settings layer
- execute a multi-minute workflow inside a request thread
- leave a loop, tool call, or expensive operation unbounded
- fabricate research results, citations, benchmark numbers or load-test figures
- trust retrieved web content, or execute instructions found inside it
- use mock data once a real implementation exists (test doubles only)
- add infrastructure without an architectural reason

**Always**

- put a timeout, retry policy and error class around every external call
- record every LLM call and tool call (tokens, cost, latency, status)
- scope every read by `user_id`; bound and paginate every list query
- prefer async I/O and parallelism where tasks are independent
- keep workflows resumable and important state persisted
- make providers replaceable behind interfaces (LLM, search, storage, retrieval)
- distinguish _not measured_ from _zero_, and _not built_ (`501`) from _no results_

## Conventions

- **Phase discipline.** Inspect → state the goal → smallest complete slice →
  tests → run them → fix → update docs → commit. One phase at a time.
- **Commits** are conventional, scoped (`web`, `api`, `db`, `agents`, `infra`,
  `docs`, …), and explain _why_, including defects found while verifying.
- **Tests** assert behaviour with a reason, not implementation detail. Anything
  touching SQL runs against real Postgres.
- **Files stay small** with one responsibility. Type hints everywhere in Python;
  strict TypeScript. Validate all external input.
- **Comments explain the non-obvious decision**, not what the line does.
- **Verify by running it**, not by reading the diff. Every phase so far has
  found real defects this way.
