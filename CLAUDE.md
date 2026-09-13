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

**Phases 0–8 of 25 are complete.** Full plan and per-phase status:
[`docs/PHASES.md`](docs/PHASES.md) — read it before starting new work.

| Layer                       | State                                                                                                                                                                                                                                               |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Frontend (`apps/web`)       | Complete product surface, 99 unit + 18 e2e tests. Runs against mock fixtures or the live API by one env var.                                                                                                                                        |
| API (`apps/api`)            | FastAPI: research surface, file uploads, SSE, authorisation, error contract, health probes. 669 tests.                                                                                                                                              |
| Database                    | PostgreSQL, 21 tables, Alembic migrations on two branches (`core`, `vector`), pgvector column sized for the declared embedding model (768). Runs survive restart.                                                                                   |
| Object storage              | `ObjectStorage` over S3/MinIO plus a filesystem backend. Bounded, classified, readiness-probed. Written by uploads and by ingestion.                                                                                                                |
| Model gateway               | `LLMGateway` over Anthropic, OpenAI and Ollama. Registry, role/mode routing, retry, failover, call ledger. Ingestion calls its embed path; generation has no caller until Phase 10.                                                                 |
| Research tools              | Six tools behind a `Toolbelt`: search, fetch, parse, SEC, arXiv, GitHub. Four-layer SSRF guard, untrusted-content type, per-call ledger. No caller yet — Phase 10.                                                                                  |
| Ingestion (`app/retrieval`) | Upload API; PDF, HTML, Markdown and text parsed in a killable, scrubbed child process; offset-exact LlamaIndex chunking; gateway embeddings; chunk metadata filters. No runtime caller yet: the worker ingests a run's attached uploads (Phase 13). |
| Retrieval (`app/retrieval`) | `PostgresRetriever` behind the `Retriever` Protocol: pgvector cosine and OR-ed Postgres full-text run concurrently, fused by reciprocal rank, reranked for diversity by MMR. Metrics and a benchmark with measured numbers. No caller until Phase 10. |
| Worker / agents             | **Does not exist.** A created run stays `queued`. Phases 9 and 13.                                                                                                                                                                                  |
| Everything else             | Not built. Endpoints for unbuilt capabilities return `501 not_implemented`.                                                                                                                                                                         |

Nothing fabricates data to fill a gap. The only benchmark executed so far is
the retrieval benchmark (Phase 8, lexical arm only); no end-to-end evaluation
has run.

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
| [`docs/ADRs/`](docs/ADRs/)                     | 13 accepted decisions. New irreversible choice ⇒ new ADR.               |

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
  app/research/    run lifecycle, repository protocol, event broker, service
  app/db/          base, models/, repositories/
  app/workers/     JobQueue interface, Redis + in-memory adapters
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
  app/agents|evidence|reports|evaluations|observability/
                   module boundaries with docstrings; filled by later phases
  migrations/      Alembic, two branches: core (relational) and vector
                   (needs pgvector). `alembic upgrade heads` applies both.
  tests/           pytest; tests/support/postgres.py provisions a real database
packages/shared-types/   TypeScript DTOs shared by web and mock API
packages/prompts|evaluation/   placeholders (Phases 5/10, 18)
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
(chunking only, imported lazily), pypdf, py3langid (language detection).
`uv` for locked deps. `ruff` + `mypy --strict`. pytest, with `moto` in server
mode for a real S3 endpoint and `httpx2.MockTransport` for the model providers.

**Planned** — LangGraph (orchestration, ADR 0002), LlamaIndex (ingestion/retrieval,
ADR 0003), OpenTelemetry,
Prometheus/Grafana, AWS ECS Fargate via Terraform (ADR 0008).

## Commands

```bash
npm install --legacy-peer-deps   # cold resolve only; npm ci afterwards
make dev            # frontend on :3000 against mock fixtures
make up             # Postgres+pgvector, Redis, MinIO, Prometheus, Grafana
make api-install && make migrate && make api    # backend on :8000
make ci             # format, lint, typecheck, unit tests, both stacks
make test-e2e       # Playwright
make migrate-check  # migration upgrade/downgrade round trip
make migration m="add x"   # autogenerate on the core line; HEAD=vector@head for pgvector
make benchmark-retrieval   # measure the retrieval strategies and the chunk size
```

Point the frontend at the real API with `NEXT_PUBLIC_API_MODE=live` and
`NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1`. Nothing else changes.

## Environment constraints on this machine

Hard-won; do not rediscover them.

- **Docker is not installed.** `make up` cannot run here. The API needs Redis
  outside `APP_ENV=test`, so use `APP_ENV=test` for local end-to-end checks.
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
- **The repo path contains a space.** Vitest's `forks` pool cannot hand off to
  workers, so the config pins `pool: 'threads'`.
- **npm cold resolve crashes** (arborist bug in the vitest peer graph) without
  `--legacy-peer-deps`. `npm ci` from the committed lockfile is fine.
- Bash heredocs fail above roughly 8 KB — use the Write tool for larger files.
- **Ollama is installed but not running**, and has pulled neither the registry's
  chat model nor `nomic-embed-text`. Live embeddings are unverified here; tests
  drive the real adapter over `httpx2.MockTransport`.
- **LlamaIndex takes about 4.5 s to import cold.** It is imported lazily inside
  the chunker, so the API process never pays it.
- **`.env.example` is committed with mixed line endings** (mostly CRLF). An
  editor that normalises them turns a small change into a block rewrite; check
  `git diff --stat --ignore-cr-at-eol` before committing.
- **The agent's file tool decodes backslash-u escape sequences** in content it
  writes into the characters they name (one became a NUL byte in a comment).
  Build non-ASCII characters with `chr()`, or type the character itself.
- **The repo stores LF and there is no `.gitattributes`.** Python's
  `Path.write_text` emits CRLF here, which turns a three-line edit into a
  whole-file diff. Write with `newline="
"`, or check `git diff --stat`
  before committing.

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
