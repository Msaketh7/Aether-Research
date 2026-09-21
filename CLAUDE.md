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

**All 25 phases are complete, plus Phase 26 (single sign-on).** Full plan and per-phase status:
[`docs/PHASES.md`](docs/PHASES.md) — read it before starting new work. Three
things are built and have never been executed, and every document says so where
it describes them: the evaluation benchmark (needs credentials, costs money per
case), the deployment (no cloud account), and the **live SSO round trip** (no
Auth0 or Supabase credentials - the flow is tested against a scripted provider,
the vendors' exact wire shapes are unconfirmed).

| Layer                                 | State                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Frontend (`apps/web`)                 | Complete product surface, 128 unit + 24 e2e tests. Runs against mock fixtures or the live API by one env var. Design system in `src/app/globals.css` and `src/app/fonts.ts`: semantic oklch colours with a readable on-tint pair per status, an elevation scale whose dark levels lead with an inset top rim, three self-hosted variable faces (Plus Jakarta Sans for the interface, Fraunces for display lines, JetBrains Mono for every figure), and five durations plus four easings that every component transitions with - no component picks its own timing. The product opens on a question box at `/` - one field, Enter to start, with mode, depth and filters in a card beside it rather than on a page of their own; the dashboard is the record of what has been asked; the rail lists recent runs and becomes a drawer below `lg`.                                                                                                  |
| API (`apps/api`)                      | FastAPI: research surface, file uploads, SSE, authentication and authorisation, rate limiting, error contract, health probes, `/metrics`, `/evaluations`, `/settings`. 1415 tests.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Database                              | PostgreSQL, 25 tables (`users` and `sessions` first written in Phase 20, `audit_log` added by it; `identities` and `refresh_tokens` added by ADR 0022, which also dropped `sessions.token_hash` - a session holds no credential any more), Alembic migrations on two branches (`core`, `vector`), pgvector column sized for the declared embedding model (768). A run's row carries the worker's lease (ADR 0017) and, since Phase 22, the clock that stops the reconciliation sweep re-dispatching a backlog it already dispatched; `research_events` carries the progress stream (ADR 0018); UUIDs come back as `uuid.UUID`, never the driver's subclass.                                                                                                                                                                                                                                                                                      |
| Object storage                        | `ObjectStorage` over S3/MinIO plus a filesystem backend. Bounded, classified, readiness-probed. Written by uploads and by ingestion.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Model gateway                         | `LLMGateway` over Anthropic, OpenAI and Ollama. Registry, role/mode routing, retry, failover, call ledger. Every agent reaches a model through it and prices its own calls with `cost_of`. Embeddings are cached per text; completions deliberately are not (ADR 0019). The embedding model is pinned by `EMBEDDING_MODEL`, not taken from registry order, and two declared without a choice is refused - an index holding vectors from two models fails silently.                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Research tools                        | Six tools behind a `Toolbelt`: search, fetch, parse, SEC, arXiv, GitHub. Four-layer SSRF guard, untrusted-content type, per-call ledger. Search, fetch and parse are cached by content hash and coalesced in flight. Called by the web and data researchers; the synthesizer and validator are given no belt at all.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Ingestion (`app/retrieval`)           | Upload API; PDF, HTML, Markdown and text parsed in a killable, scrubbed child process; offset-exact LlamaIndex chunking; gateway embeddings; chunk metadata filters. Called by the web and data researchers, which is what turns a fetched page into a citable source. The worker ingests a run's attached uploads (Phase 13).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Retrieval (`app/retrieval`)           | `PostgresRetriever` behind the `Retriever` Protocol: pgvector cosine and OR-ed Postgres full-text run concurrently, fused by reciprocal rank, reranked for diversity by MMR. Metrics and a benchmark with measured numbers. Called by the document researcher and by evidence extraction.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Research graph (`app/agents`)         | LangGraph `StateGraph` over a typed, checkpointed `ResearchState`: parallel researchers via `Send`; every node wrapped with cancellation, the FR-8 ceilings, a timeout and usage accounting; Postgres checkpoints whose tables Alembic owns, read back through a derived allowlist. Every node is implemented.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Agents (`app/agents`)                 | All nine: planner, three researchers behind a router, evidence, claim normalization, verification, contradictions, critic, synthesis, and a citation validator that calls no model. Versioned prompts as package data; a model cites by catalogue number and never emits an identifier (ADR 0015).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Evidence (`app/evidence`)             | The chain made durable: a projection of graph state onto claims, evidence spans and contradictions, idempotent on derived ids; source deduplication into clusters, so corroboration counts distinct content rather than copies; origin credibility from a declared table. `/sources` and `/evidence` serve real rows.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Reports (`app/reports`)               | A validated draft assembled into the report a reader opens: claim numbers renumbered to citation ordinals, Evidence and References built from rows rather than written, citations whose foreign keys the database enforces. `/report` serves it with the citation check's verdict.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| Worker                                | `python -m app.workers.runner`: claims a run against its row, ingests its attachments, streams the graph while renewing the lease and writing the run's phase, progress and counts, then records the outcome. Retries with backoff, hands runs back on shutdown, reconciles what the queue lost. Narrates the run as it goes, writes the trace and the call ledger, holds the run to its cost ceiling, and exposes Prometheus metrics on its own port.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Streaming (`app/research`)            | The events the worker emits, carried across processes: `eventbus.py` is Redis pub/sub with a capped buffer over a durable log; `events.py` is the vocabulary and the in-process transport. `publish` numbers an event by storing it, so two emitters can never share an `id:` (ADR 0018).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Caching (`app/cache`)                 | Search answers, fetched pages, extracted articles and embeddings, keyed by content hash and coalesced in flight. A closed namespace list is how "never cache per-user data globally" is kept structurally. Redis in a deployment, bounded in-memory under `APP_ENV=test` (ADR 0019).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| Ledger (`app/observability`)          | Every node execution is an `agent_runs` row, with the tool and model calls it made hanging from it; the current span travels in a ContextVar so a belt shared by the process can still say which step called it. `/activity` serves real rows (ADR 0020).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Cost governance                       | A run's ceiling is enforced _before_ each call, not only between nodes: a refusal ends discovery and the run still writes its report. An unpriced model is refused under a budget. Spend is counted from the ledger, never from a second tally.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| Telemetry (`app/observability`)       | Prometheus metrics on both processes, OpenTelemetry spans at every seam, `trace_id`/`span_id` on the trace rows, a Grafana dashboard in `infra/monitoring`, LangSmith off unless asked. `/evaluations/system` is measured from rows; every field is nullable and null means _not measured_.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Evaluation (`app/evaluations`)        | A versioned dataset in `data/eval/cases`, structural scorers, configurable gates stored with each result, `make evaluate`, and `/evaluations`. **Nothing has been executed**: running it needs credentials and spends money per case.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Load testing (`app/loadtest`)         | Two arms, both measured: `make loadtest` drives 10/25/50/100 concurrent research jobs through the real worker and graph with the model and socket scripted at a declared latency; `make loadtest-api-local` drives the HTTP surface with Locust. `breakdown.py` reads where a run's time went back out of `agent_runs`, and the driver counts Postgres's own committed transactions. Numbers: [`docs/load-testing.md`](docs/load-testing.md), artefacts in `data/loadtest/`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Scenario tests (`tests/scenarios`)    | The fifteen situations Phase 19 requires the system to survive, each driven through the whole vertical slice: queue, worker, lease, graph, nine real agents, the real toolbelt over a scripted socket, ingestion, retrieval, the projections and real Postgres. Only the model and the socket are replaced. `catalogue.py` holds the list as code and fails the build when a scenario loses its test.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Security (`app/auth`, `app/security`) | Argon2id passwords **and single sign-on** - Google and GitHub brokered by Auth0 or Supabase behind one `IdentityProvider` interface. Since ADR 0022 the credential is a short-lived signed access token in an `HttpOnly` cookie, with a refresh token path-scoped to the refresh endpoint, rotated on every use and revoking its whole family on reuse; a revocation index keyed by `sid` is what makes per-device sign-out land on the next request. Token-bucket rate limiting per identity and route class, the client address resolved through _declared_ proxy hops, and an append-only `audit_log` written outside the request's transaction (ADR 0021's rate-limiting half still stands). The development identity still exists, gated by the `local`/`test` allowlist and by `DEV_IDENTITY_ENABLED`, which can only close it further. **The live provider round trip has never run** - no Auth0 or Supabase credentials on this machine. |
| Infrastructure (`infra/`)             | Two images (api serves api, worker and migrate; web is the Next standalone build), a compose stack that runs the whole system behind the `app` profile, a modular Terraform root for ECS Fargate that `terraform validate` accepts, and a Kubernetes manifest set as the escape hatch. **None of it has been applied or built here** - there is no Docker and no AWS account on this machine; `tests/test_infrastructure.py` is what holds all three descriptions to the code they deploy.                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| CI/CD (`.github/workflows`)           | Five workflows split by cost: `ci` (fast gates plus one aggregate check to require on the branch rule), `test` (the suites, with real Postgres and Redis), `build` (both images: built, run, scanned, pushed by digest), `eval` (the benchmark, guarded on credentials and an explicit variable), `deploy` (build, migrate, roll, smoke, roll back). `deploy` and `eval` **have never run** - no AWS account, no provider credentials. `actionlint` with `shellcheck` passes on all five; `tests/test_workflows.py` asserts the decisions those tools cannot see.                                                                                                                                                                                                                                                                                                                                                                                |
| Everything else                       | Not built. Nothing on the `/api/v1` surface returns `501` any more - Phase 20 implemented the last of it - but `NotImplementedYet` stays in the taxonomy for the next capability that is declared before it is built.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |

Nothing fabricates data to fill a gap. Two benchmarks have been executed: the
retrieval benchmark (Phase 8, lexical arm only) and the load test (Phase 21 and
22, 370 research runs through the real pipeline plus four Locust ladders over
the HTTP surface and a worker-concurrency sweep).

**Live model runs have now happened**, and they found a defect nothing scripted
could. `tests/scenarios/test_live_model.py` drives the whole vertical slice with
the real OpenAI adapter behind the real gateway - opt-in behind
`AETHER_LIVE_MODEL=1` and a key, skipped otherwise, about three cents and ninety
seconds a run. Six runs produced 1 to 5 claims each and every one produced a
report whose citations resolved to spans still present at their recorded
offsets. What it caught: a provider reports the **dated snapshot** an alias
resolved to (`gpt-4o-mini` comes back as `gpt-4o-mini-2024-07-18`), the registry
matched the returned id exactly, so every priced call was recorded as uncosted -
which reads as "not measured" and makes a budgeted run stop discovery early. A
scripted provider echoes back the id it was handed, so the two always agreed.
`tests/scenarios/test_live_web.py` removes the last substitution and goes to the
open internet; it is **written but never run**, because that needs a search
provider key this machine does not have.

**No end-to-end evaluation has run.** The suite exists and will produce numbers
the moment someone runs it with credentials; until then every gate is ungated
and every unmeasured metric reads as _not measured_ rather than as zero.

**Licensing.** Proprietary, all rights reserved - see [`LICENSE`](LICENSE). The
repository is public on GitHub for reading, not reuse. Never add an open-source
license or `"license": "MIT"` to a manifest; `apps/api/tests/test_licensing.py`
fails the build if one appears. Code copied in from open-source projects must be
listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

## Specifications (authoritative)

| Document                                       | Contents                                                                   |
| ---------------------------------------------- | -------------------------------------------------------------------------- |
| [`docs/PHASES.md`](docs/PHASES.md)             | The 25-phase build plan and what is done                                   |
| [`docs/PRD.md`](docs/PRD.md)                   | Product requirements, FR-1..FR-10, three product modes                     |
| [`docs/TDD.md`](docs/TDD.md)                   | Technical design: components, agent graph, §7.2 schema, §18 API surface    |
| [`docs/architecture.md`](docs/architecture.md) | System map                                                                 |
| [`docs/threat-model.md`](docs/threat-model.md) | STRIDE per trust boundary; prompt injection and SSRF                       |
| [`docs/evaluation.md`](docs/evaluation.md)     | Metrics, thresholds, the no-fabricated-numbers rule                        |
| [`docs/ADRs/`](docs/ADRs/)                     | 22 decisions (0021 superseded by 0022). New irreversible choice ⇒ new ADR. |

## Repository structure

```
apps/web/          Next.js frontend
  src/app/         pages: (app)/ is the question box and the home screen,
                   plus dashboard, research/*, evaluations, settings; login
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
  app/auth/        who is calling: passwords.py (Argon2id, off the event
                   loop, plus the timing equaliser), tokens.py (the signed
                   access token and the opaque rotating refresh token),
                   sessions.py (start, refresh with reuse detection, verify,
                   revoke), revocation.py (the index that makes a sign-out land
                   on the next request - authoritative state, deliberately not
                   in app/cache), transactions.py (the half-finished sign-in:
                   state, nonce and PKCE verifier, single-use), federation.py
                   (verified provider claims -> an account; linking on subject,
                   never on email), redirects.py (the open-redirect guard,
                   mirrored in the frontend), cookies.py (every Set-Cookie
                   attribute in one place), service.py (register and sign in,
                   and the rules that make their failures indistinguishable),
                   principal.py (a request -> a caller, and the development
                   identity's allowlist), providers/ (base.py is the
                   IdentityProvider seam, oidc.py the shared protocol, jwks.py
                   the cached signing keys, registry.py the two vendor
                   descriptions)
  app/security/    controls on the public surface itself: ratelimit.py (token
                   buckets, Redis Lua or in-memory), forwarded.py (the client
                   address through declared proxy hops), audit.py (the
                   request-scoped recorder behind audit_log)
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
  app/loadtest/    the measuring apparatus (Phase 21): profiles.py (the four
                   declared loads), measure.py (percentiles, the level
                   sampler), results.py, report.py. Driven by
                   scripts/loadtest.py; loadtest/locustfile.py is the HTTP arm.
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
.github/workflows/  ci.yml, test.yml, build.yml, eval.yml, deploy.yml (Phase 24)
scripts/smoke.py    the post-deploy check: stdlib only, asks a deployment from
                    outside whether it works rather than whether it is running
scripts/screenshots.mjs  drives the real app in a real browser and writes
                    docs/screenshots/ - `make screenshots`, so the README's
                    pictures are regenerated rather than re-taken
infra/docker/       api.Dockerfile (api + worker + migrate) and web.Dockerfile,
                    both built from the repository root
infra/terraform/    the AWS root: modules/{network,security,database,cache,
                    storage,alb,ecs-cluster,ecs-service,secrets} and one
                    .tfvars per environment. Validated, never applied.
infra/kubernetes/   the portability escape hatch ADR 0008 names. Never applied.
infra/monitoring/   Prometheus scrape config and the Grafana dashboard
```

## Tech stack (as pinned)

**Frontend** — Next.js 16, React 19, TypeScript 5.9 strict (+`noUncheckedIndexedAccess`),
Tailwind v4, shadcn-style components in-repo, TanStack Query 5, native `EventSource`,
Zod 4. Vitest 4, Playwright 1.63.

**Backend** — Python 3.12+, FastAPI, Pydantic v2 (+`email-validator`),
argon2-cffi (Argon2id), SQLAlchemy 2 async, Alembic,
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
npm run app         # EVERYTHING: migrate, API, worker, web - one command, no Docker.
                    # `make start` and `python scripts/dev.py` are the same thing;
                    # make is NOT installed on this machine, so npm is the one to use.
npm run app:api     # the same without the frontend
make dev            # only the frontend, on :3000 against mock fixtures
make up             # Postgres+pgvector, Redis, MinIO, Prometheus, Grafana
make api-install && make migrate && make api    # backend on :8000
make ci             # format, lint, typecheck, unit tests, both stacks
make test-e2e       # Playwright
make migrate-check  # migration upgrade/downgrade round trip
make worker         # the research worker (needs Postgres and Redis)
make migration m="add x"   # autogenerate on the core line; HEAD=vector@head for pgvector
make test-scenarios        # the fifteen end-to-end research scenarios (part of api-test)
make benchmark-retrieval   # measure the retrieval strategies and the chunk size
make loadtest              # 10/25/50/100 concurrent research jobs; provisions its own database
make loadtest-api-local    # Locust over the HTTP surface, against a disposable local stack
make evaluate              # run the evaluation dataset (real runs; costs money)
make audit                 # pip-audit + npm audit against what is installed
make secrets               # gitleaks over the working tree and its history
make tf-validate           # terraform fmt + validate (no credentials needed)
make images                # build both container images (needs Docker)
make up-app                # the whole stack in containers (needs Docker)
make screenshots           # regenerate the README screenshots (needs `make dev` running)
```

Point the frontend at the real API with `NEXT_PUBLIC_API_MODE=live` and
`NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1`. Nothing else changes.

## Environment constraints on this machine

Hard-won; do not rediscover them.

- **Docker is not installed, and neither is Redis.** `make up`, `make up-app`
  and `make images` cannot run here, so the Phase 23 images have never been
  built on this machine - CI builds them. **Nor are `terraform`, `actionlint`,
  `shellcheck` or `gitleaks`**: each was fetched into the session's scratch
  directory to verify its part of Phases 23 and 24 once, and CI installs them
  properly. That is the pattern to repeat rather than assuming a check cannot
  be run here - a pinned release binary is usually a two-minute download, and
  every one of them found something.
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
- **`npm run app` provisions a _persistent_ one the same way**, under
  `.data/postgres` on port 55432 with trust auth, because the real PostgreSQL 17
  service on 5432 has no `aether` role and its `postgres` password is not known
  here - `createuser` against it fails with `password authentication failed`.
  The managed cluster needs no password because the launcher creates it. It is
  reused across runs and stopped on a clean exit; a leftover one is harmless
  because the next start reuses it rather than failing.
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
- **A semantic hue is a fill colour, not a text colour.** A status chip that
  paints `bg-success/15` and writes `text-success` on top measured 3.3:1 in
  light and 3.1:1 in dark, under the 4.5:1 a 12px label needs, and every badge
  in the product had the same bug. `globals.css` therefore carries a second set
  of tokens - `--success-strong` and friends - which is the same hue carried to
  a lightness that can be read, in opposite directions per theme. Fills, bars
  and icons keep the base colour; anything that is text takes the `-strong` one.
  `scripts/` has no permanent checker for this: measure it by compositing the
  real background stack onto a 1x1 canvas, because `getComputedStyle` returns
  `oklab()` and `lab()` here and parsing those as RGB silently produces numbers
  that look plausible and are wrong.

- **`color-scheme` is what themes native controls.** The date input's calendar
  button, its picker panel and the scrollbars are drawn by the browser, not by
  this stylesheet, and without `color-scheme: dark` on `.dark` the calendar
  glyph stayed near-black on a near-black field and disappeared.

- **Never put an entry animation on markup that is present at first paint.**
  A browser does not run transitions during a document's initial style
  resolution, so an element that starts hidden has nothing to move it and stays
  invisible - and a keyframe with `animation-fill-mode: both` fails the same way
  whenever motion is cancelled, which `prefers-reduced-motion` does. The
  dashboard's heading and question box were photographed blank exactly twice
  that way while looking correct in a browser, because `scripts/screenshots.mjs`
  captures with `reducedMotion: 'reduce'`. `.reveal` and `.stagger` in
  `globals.css` are therefore transitions out of `@starting-style` whose resting
  state is visible, and they are only ever used on what a query, an event or an
  interaction puts on the page. Verify UI motion by capturing it, not by
  watching it: a browser is the one environment where this class of bug hides.

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
- **A test that starts a server needs a two-minute deadline, not thirty
  seconds.** `tests/test_smoke_script.py` runs the real application on a real
  socket, which means importing it - about six seconds warm - and the suite runs
  four-wide under xdist on a CPU that never turbos. A 30 s startup deadline
  passed every time the module was run alone and failed in the full run. Same
  class as the scenario timeouts below: these are hang detectors, and tightening
  one turns load into a red suite.
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
- **`argon2` and `email-validator` are the two dependencies Phase 20 added**, and
  `argon2` is imported lazily like every other heavy one: half a second, inside
  the function that builds the hasher, so the worker - which authenticates
  nobody - never pays it. A password hash is ~130 ms on this machine at the
  shipped parameters, which is why every hash and verify runs in a thread; the
  tests use the real parameters rather than weakened ones, so an auth test costs
  about 150 ms and that is correct.
- **Tests that need a signed-out caller use the `strict_client` fixture**, which
  is the ordinary test settings with `dev_identity_enabled=False`. Not a
  different `APP_ENV`: that also selects the queue, the cache, the storage
  backend and the rate-limit backend, so pretending to be `staging` in order to
  test sign-in would mean needing Redis and S3 to test sign-in.
- **`make` is not installed on this machine.** Every `make ...` in this file is
  a description of the workflow, not a runnable command here - use the npm
  script or the underlying command. `npm run app` is the single local
  entry point (`scripts/dev.py`).
- Bash heredocs fail above roughly 8 KB — use the Write tool for larger files.
- **The dev server does not hydrate on `127.0.0.1`, only on `localhost`.**
  Turbopack's HMR socket rejects the literal address (`ERR_INVALID_HTTP_RESPONSE`
  on the handshake), and in Next 16 a failed HMR handshake leaves the page
  server-rendered but never hydrated - no client fetches, no page error, just an
  app with a correct shell and an empty body. `scripts/screenshots.mjs`
  photographed exactly that before the host was changed. The Playwright config
  uses `127.0.0.1` and is fine, because its `webServer` runs `next start` rather
  than `next dev`.
- **Ollama is installed but not running**, and has pulled neither the registry's
  chat model nor `nomic-embed-text`. Live embeddings are unverified here; tests
  drive the real adapter over `httpx2.MockTransport`.
- **LlamaIndex takes about 4.5 s to import cold.** It is imported lazily inside
  the chunker, so the API process never pays it.
- **The secret scan has one allowance, and it needs one.** gitleaks'
  `generic-api-key` rule matches the _key_ `OPENAI_API_KEY=` in `.env.example`
  and then scores the entropy of the following line, which is a comment - a
  false positive on a file whose entire design is that every credential is
  present and blank. `.gitleaks.toml` skips the file, and
  `test_config.py::test_the_shipped_example_carries_no_credential_values` is
  what makes that safe: it walks the `SecretStr` fields and fails if any of
  them loads a value from the example. The two exceptions are MinIO's
  `minioadmin`, pinned to that exact string.
- **`.env.example` is committed with CRLF line endings.** An editor that
  normalises them turns a small change into a whole-file rewrite; check
  `git diff --stat --ignore-cr-at-eol` before committing. This shell's `grep -c
$'
'` and `cat -A` both report the file as LF, which is wrong - read it in
  Python with `newline=""` to see what is really there.
- **A quoted bash heredoc still loses `\\`.** `<<'EOF'` is supposed to be
  literal, but a `'\\'` written inside one arrives in the file as a single
  backslash - which silently turned the open-redirect guard's
  `startsWith('/\\')` into an unterminated string literal, and turned a
  test's `'/\\evil.example'` into `'/evil.example'`, so the assertion
  passed against the wrong input. Same class as the backslash-u note below.
  Write such files with Python and build the character with `chr(92)` - and
  remember a literal backslash in a **TypeScript source string** needs two.
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
