# Build plan: the 25 phases

The original brief, preserved as the working memory of this project. It is the
source of truth for **what to build next** and **what "done" means** for each
phase, across sessions.

**Rule:** phases are built in order, one at a time. At the start of a phase —
inspect the repository, state the goal, implement the smallest complete slice,
add tests, run them, fix failures, update documentation, commit. Only then move
on. Never generate thousands of lines without validating them.

Status legend: **Done** · **Next** · **Planned**

| #   | Phase                      | Status   |
| --- | -------------------------- | -------- |
| 0   | Repository initialisation  | Done     |
| 1   | Frontend product prototype | Done     |
| 2   | Backend foundation         | Done     |
| 3   | Database                   | Done     |
| 4   | Storage                    | Done     |
| 5   | Model abstraction          | Done     |
| 6   | Web research tools         | Done     |
| 7   | Document ingestion         | **Next** |
| 8   | Retrieval                  | Planned  |
| 9   | LangGraph agent system     | Planned  |
| 10  | Agents                     | Planned  |
| 11  | Evidence system            | Planned  |
| 12  | Report generation          | Planned  |
| 13  | Background workers         | Planned  |
| 14  | Streaming                  | Planned  |
| 15  | Caching                    | Planned  |
| 16  | Cost and token governance  | Planned  |
| 17  | Observability              | Planned  |
| 18  | Evaluation framework       | Planned  |
| 19  | Testing                    | Planned  |
| 20  | Security                   | Planned  |
| 21  | Load testing               | Planned  |
| 22  | Optimization               | Planned  |
| 23  | Infrastructure             | Planned  |
| 24  | CI/CD                      | Planned  |
| 25  | Documentation              | Planned  |

---

## The product

A user enters a complex research question, for example: _"Compare the major AI
inference infrastructure companies. Analyze their products, technology, pricing,
funding, financial performance, recent announcements, risks, competitive
advantages, and market opportunities."_

The platform must: understand the question; decompose it into subtasks; execute
them in parallel; search the web and external sources; retrieve from indexed
documents; extract claims and evidence; detect contradictory evidence; decide
whether more research is needed; iterate when it is; synthesise a final report;
attach citations to factual claims; validate those citations; persist all state;
stream progress to the frontend; expose history and evidence; and provide
evaluation and observability dashboards.

Prefer a **modular monolith** with independently scalable API and worker
processes. Strong typing, validation, tests, structured logs, configuration
management, explicit interfaces. Every important architectural decision gets an
ADR under `docs/ADRs/`.

---

## Phase 0 — Repository initialisation · **Done**

Monorepo: `apps/{web,api}`, `packages/{shared-types,prompts,evaluation}`,
`data/{seed,fixtures,eval}`, `docs/` (PRD, TDD, architecture, threat-model,
evaluation, ADRs), `infra/{docker,terraform,kubernetes,monitoring}`, `scripts/`,
`.github/workflows/`. Plus README, LICENSE, Makefile, docker-compose.yml,
.env.example. Git initialised, pre-commit hooks where useful, conventional
commits, clean initial commit.

_Landed:_ commit `a4b3aff`. ADRs 0001–0009. Local stack: Postgres+pgvector,
Redis, MinIO, Prometheus, Grafana.

## Phase 1 — Frontend product prototype · **Done**

Next.js, TypeScript, App Router, Tailwind, shadcn/ui, TanStack Query, strict
mode. Pages: `/login`, `/dashboard`, `/research/new`, `/research/[id]` and its
`activity`, `sources`, `evidence`, `report` tabs, `/evaluations`, `/settings`.
Operate against mock APIs first — do not wait for the backend.

Must feel like a research product, not a chatbot. Dashboard: history, recent
reports, status, quick-start. Creation: question, mode, depth, optional domains,
optional date range. Activity: live timeline (Planning → Searching → Reading
sources → Extracting evidence → Verifying claims → Detecting contradictions →
Writing report). Report: executive summary, key findings, detailed sections,
claim/evidence cards, citations, source list, confidence. Sources: title,
publisher, URL, publication date, type, relevance. Evidence: claims, supporting
and contradicting evidence, confidence. Responsive. Reusable components, not
giant page files. Unit tests plus Playwright smoke tests.

_Landed:_ commit `91bfdee`. Mocking happens at the network boundary (ADR 0009):
route handlers implement the real contract including a `text/event-stream`
endpoint, so switching to the backend is one env var. 94 unit + 18 e2e tests.

## Phase 2 — Backend foundation · **Done**

Python 3.12+, FastAPI, Pydantic, SQLAlchemy, Alembic, PostgreSQL, Redis.
Structure under `apps/api/app/`: `api`, `core`, `auth`, `research`, `agents`,
`retrieval`, `sources`, `evidence`, `reports`, `evaluations`, `observability`,
`db`, `workers`. Health endpoints `GET /health`, `GET /ready`. Endpoints:
`POST /api/v1/research`, `GET /api/v1/research/{id}`, `GET /api/v1/research`,
`.../sources`, `.../evidence`, `.../report`, `.../events`.

**Return `202 Accepted` when a research job is created. Never execute the
complete research workflow inside the request thread.**

_Landed:_ commit `1d7ac6a`. 96 tests including a cross-language contract test
that fails if the Python and TypeScript vocabularies diverge.

## Phase 3 — Database · **Done**

PostgreSQL. Tables: `users`, `research_projects`, `research_runs`,
`research_tasks`, `sources`, `documents`, `document_chunks`, `claims`,
`evidence`, `citations`, `contradictions`, `agent_runs`, `tool_calls`,
`llm_calls`, `reports`, `report_sections`, `evaluations`, `feedback`. UUID
primary keys, timestamps, indexes, foreign keys, pgvector enabled, Alembic
migrations, connection pooling. Never perform unbounded queries. Repository and
service layers where useful.

_Landed:_ commit `8d4bc5e`. 19 tables (adds `sessions`). Two migrations —
pgvector is split out because the extension is a server-side prerequisite. 128
tests against a real, migrated Postgres.

## Phase 4 — Storage · **Done**

S3-compatible object storage behind an `ObjectStorage` abstraction with
`upload()`, `download()`, `delete()`, `exists()`. Store PDFs, raw source HTML,
normalised documents, screenshots, evaluation artifacts, and generated reports
where appropriate. MinIO locally through Docker Compose; AWS S3 in production.

_Landed:_ ADR 0010. `app/storage/` with a keyed namespace
(`runs/{run_id}/{kind}/{name}`, content-addressed on the SHA-256 the schema
already stores) and two implementations — `S3ObjectStorage` for MinIO and AWS,
`FilesystemObjectStorage` for tests and Docker-less development, refused in
production. Every call is bounded by a timeout, a retry policy and a 25 MiB
ceiling enforced on read as well as write; `download_stream` is the escape
hatch. Failures are translated into three classes with different retry
semantics. The artifact store is now a readiness dependency, probed by a HEAD on
the bucket itself. 74 new tests, the shared contract run against both backends,
with the S3 one talking HTTP to a real S3 server rather than a patched botocore.

Defect found by running it: `aiobotocore`'s `async with body as stream` yields
the wrapped aiohttp response, not the proxy, so `stream.read(n)` raised
`TypeError` and every chunked download failed. Streaming now reads through
`iter_chunks` on the unbound body.

## Phase 5 — Model abstraction · **Done**

Provider-neutral `LLMProvider` interface: `generate()`, `generate_structured()`,
`stream()`, `embed()`. Implement OpenAI, Anthropic and Ollama. Select by
configuration. **Never instantiate a provider inside an agent node.** Add a
`ModelRegistry` and a `ModelRouter` routing by agent role, research mode, cost
tier and provider — planner to a cheaper model, researcher to medium/strong,
critic to strong, synthesizer to the strongest configured. Ollama keeps local
development free of API keys. (ADR 0007 already records this design.)

_Landed:_ `app/models/` — an `LLMProvider` protocol with all four operations
plus `count_tokens`, implemented for Anthropic, OpenAI and Ollama; a
YAML-declared `ModelRegistry`; a `ModelRouter` resolving (role, mode) to a tier
and a bounded fallback chain; and an `LLMGateway` that is the only door to a
model. Every call is bounded by a timeout, a retry policy with jitter, a
concurrency semaphore and a per-model output ceiling, and every _attempt_ is
recorded with tokens, cost, latency and status.

Capabilities are declared rather than assumed, which is what "providers are
replaceable" actually costs: current Anthropic models reject `temperature` with
a 400, Anthropic has no embeddings API at all, and neither OpenAI nor Ollama can
count a prompt's tokens before spending them. Each is a flag on the model spec or
an explicit `CapabilityNotSupported`, never a silent degradation.

Prices are dated and sourced, and a model whose price this repository cannot
verify ships **unpriced** — `cost_usd` then returns `None`, not `0.0`, so
Phase 16's budget ledger can tell an uncosted call from a free one. The shipped
registry declares Anthropic and Ollama models; the OpenAI adapter is complete and
tested but declares none, because a guessed model id is a hard 404 and a guessed
price silently corrupts every cost report built on it.

Divergence recorded: TDD 6.1's illustrative YAML routes the planner to the strong
tier. ADR 0007 and this build plan both route it to the cheap tier; the
implementation follows them and TDD 6.1 has been amended to match.

80 new tests, 282 total. The adapters are tested through the real vendor SDKs
with only the socket replaced, so request shape, stream decoding and the SDK
exception classes all execute.

Defect found by running it: the embeddings-only model was being offered as a
fallback in _chat_ routing chains, where a failover onto it would have produced a
confusing 400 instead of an answer. Chat capability is now a declared flag the
router filters on.

## Phase 6 — Web research tools · **Done**

Tools: `web_search()`, `fetch_url()`, `extract_content()`, `search_sec()`,
`search_arxiv()`, `search_github()`. Each needs a strict input schema, timeout,
retries, structured return type, logging, metrics and error classification.
**No arbitrary shell commands.** URL validation and SSRF protection blocking
localhost, 127.0.0.1, private ranges, cloud metadata endpoints and unsafe
schemes. Retrieved content is untrusted data; never execute instructions found
in a page.

_Landed:_ ADR 0011. `app/sources/` with the six tools behind a `Toolbelt`, one
guarded HTTP client, and a `ToolExecutor` that bounds, retries, classifies and
records every call.

Two controls are enforced by construction rather than by convention, which is
what the ADR records. Retrieved text is `UntrustedText`, whose `__str__`
**raises** — so `f"Summarise: {page.body}"` is a `TypeError` at the moment it is
written rather than a prompt injection in production; reaching the characters
means choosing `for_prompt()` (delimited, with a standing data notice) or
`expose()` (storage and hashing). And every outbound request goes through one
client whose SSRF guard has four layers: scheme/credential/port checks, DNS
resolution before the request, **every** resolved address checked rather than the
first, and the **connected peer verified** against that set before a byte of body
is read. Redirects are followed manually and re-validated at every hop.

A `Toolbelt` is a capability object: the synthesizer's is empty, and there is no
shell, filesystem or code-execution tool — not disabled, absent.

130 new tests, 412 total. The SSRF cases are written as attacks rather than as
coverage — cloud metadata, loopback in six spellings, RFC 1918 including the
172.16/12 range hand-written blocklists miss, and the numeric encodings.

Two defects found by running it, both real security gaps:

- `http://127.1/` and `http://0177.0.0.1/` are loopback, and the classifier
  missed them — they were refused only because Windows DNS happened to fail,
  and would have reached the resolver on Linux. The full `inet_aton` grammar is
  now implemented.
- `httpx.AsyncClient(cookies=None)` means "no _initial_ cookies", not "no cookie
  jar". A `Set-Cookie` from the first hop of a redirect was being stored and
  replayed on the second. The jar is now emptied before every request.

Verified end to end against the live internet as well as against mocks: a real
130 KB page fetched over real DNS and TLS with robots.txt honoured, extracted to
4.7 KB of article text, while the same client refused the metadata endpoint.

## Phase 7 — Document ingestion · **Next**

Upload, parsing, metadata extraction, chunking, embedding, indexing — using
LlamaIndex where appropriate (ADR 0003). Support PDF, HTML, Markdown, TXT.
pgvector initially. Store document, chunk, embedding, metadata. Metadata
filtering.

## Phase 8 — Retrieval · Planned

Hybrid retrieval: dense vector search, BM25/lexical, metadata filtering, rank
fusion, reranking. A `Retriever` interface with `retrieve()`,
`retrieve_with_filters()`, `retrieve_hybrid()`. Benchmark the strategies. Do not
hard-code a chunk size without documenting the decision. Retrieval evaluation
tests.

## Phase 9 — LangGraph agent system · Planned

LangGraph as the orchestration layer (ADR 0002). A typed `ResearchState` holding
`research_id`, `query`, `research_plan`, `subtasks`, `sources`, `claims`,
`evidence`, `contradictions`, `completed_tasks`, `failed_tasks`, `iteration`,
`token_usage`, `estimated_cost`, `critique`, `report`.

Graph: START → Planner → Research Fan-Out → Evidence Extraction → Claim
Normalization → Verification → Critic → Conditional Re-Plan → Synthesis →
Citation Validation → END. Researchers run in parallel where possible. **Bound
every loop**: `MAX_RESEARCH_ITERATIONS`, `MAX_SOURCES`, `MAX_SEARCH_QUERIES`,
`MAX_RUNTIME`, `MAX_ESTIMATED_COST`.

## Phase 10 — Agents · Planned

`PlannerAgent`, `WebResearchAgent`, `DocumentResearchAgent`, `DataResearchAgent`,
`EvidenceAgent`, `VerificationAgent`, `CriticAgent`, `SynthesisAgent`,
`CitationValidator`. Each with explicit instructions, limited tools, structured
input/output, timeout, retry policy, observability and unit tests. Agents
communicate through structured state, not unstructured string blobs.

## Phase 11 — Evidence system · Planned

Entities: `Source`, `Claim`, `Evidence`, `Citation`, `Contradiction`. Every claim
linkable to evidence; every evidence record identifies its source; every source
carries title, url, publisher, `published_at`, `accessed_at`, `content_hash`,
`source_type`. Implement claim extraction, evidence extraction, claim
normalization, source deduplication and contradiction detection. **Do not
silently resolve conflicting information — surface conflicts in the report.**

## Phase 12 — Report generation · Planned

Structured report: Executive Summary, Key Findings, Detailed Analysis,
Competitive Landscape, Evidence, Contradictions, Risks, Opportunities,
Conclusion, References. Pydantic models for the schema. Citations by source id.
A citation validator that verifies the source exists, the source was actually
retrieved, the claim is linked to evidence, the evidence belongs to the source,
and the citation is not fabricated.

## Phase 13 — Background workers · Planned

Redis-based workers: API → Redis queue → worker → LangGraph → PostgreSQL. Worker
processes separate from API processes. Job statuses: queued, running, paused,
completed, failed, cancelled. Retries, idempotency, persisted workflow
state/checkpoints. **Research must survive worker restarts.**

## Phase 14 — Streaming · Planned

Server-Sent Events. Frontend receives `research_started`, `planner_started`,
`planner_completed`, `search_started`, `source_found`, `source_processed`,
`claim_extracted`, `verification_started`, `critic_started`,
`additional_research_requested`, `synthesis_started`, `report_completed`,
`research_failed`. Store important events server-side.

_Partially in place:_ the SSE endpoint, frame format, `Last-Event-ID` replay and
the event vocabulary shipped in Phase 2. This phase adds the Redis pub/sub
broker so multiple API replicas work, and real event emission from the worker.

## Phase 15 — Caching · Planned

Redis caching for search results, URL content, embeddings and safe deterministic
transformations. Content hashes as keys. Invalidation rules. Never cache
sensitive per-user information globally. Deduplicate simultaneous identical
fetch/search operations.

## Phase 16 — Cost and token governance · Planned

Every LLM call records provider, model, prompt tokens, completion tokens, total
tokens, latency, estimated cost, research_id, agent and timestamp. Per-run
budgets. When a budget is exceeded, stop the workflow safely and return a
partial result.

_In place:_ the `llm_calls` table and the `RunLimits` frozen per run (Phase 3).

## Phase 17 — Observability · Planned

OpenTelemetry tracing across HTTP request, research run, agent node, LLM call,
tool call, retrieval and database queries where practical. Prometheus metrics,
Grafana dashboards, structured JSON logs. Track research success/failure rate,
P50/P95/P99 latency, LLM latency, tool latency, tokens, cost, cache hit rate,
retrieval metrics, queue depth, active workers, database pool saturation.
Integrate LangSmith when configured; the system stays usable when it is not.

## Phase 18 — Evaluation framework · Planned

`data/eval/` dataset; each case has question, expected topics, expected source
types and expected claims where practical. An evaluation runner. Metrics —
Retrieval: Recall@K, Precision@K, MRR, NDCG. Generation: correctness,
groundedness, faithfulness, citation precision, citation recall. Agent: task
completion, tool selection accuracy, unnecessary tool calls, planning quality,
recovery rate. System: latency, cost, failure rate. Evaluation reports and
thresholds (e.g. citation correctness ≥ a configurable gate). **Never fabricate
benchmark results; display only measured values.**

## Phase 19 — Testing · Planned

pytest, Vitest, Playwright, integration tests, agent workflow tests, retriever
tests, evaluation tests. Scenarios that must be covered: successful deep
research; search provider timeout; LLM timeout; LLM rate limit; worker restart;
duplicate URL; contradictory sources; no useful sources; empty search results;
budget exceeded; maximum iterations reached; malicious prompt injection in
source content; SSRF attempt; user cancellation; research resume after
interruption.

## Phase 20 — Security · Planned

Authentication, authorisation, per-user research access, rate limiting, request
validation, SSRF prevention, prompt injection defences, content sanitisation,
secret management, security headers, audit logs. Never expose API secrets to
Next.js client code. Dependency scanning; container image scanning if practical.

_In place:_ authorisation, request validation, secret handling, security headers
(Phases 2–3). Authentication itself is this phase.

## Phase 21 — Load testing · Planned

Locust or k6 scenarios at 10, 25, 50 and 100 concurrent research jobs. Measure
throughput, queue depth, completion rate, P50/P95/P99, database and Redis
utilisation, worker utilisation, LLM throttling. **Do not fake performance
numbers** — generate benchmark reports from actual tests.

## Phase 22 — Optimization · Planned

Profile first. Then, based on measurements: parallel agent execution, async I/O,
connection pooling, Redis caching, search-result deduplication, embedding
caching, smaller models for simple tasks, model routing, context trimming,
source compression, retrieval filtering, bounded concurrency. Document
before/after metrics. **Do not optimize without measurement.**

## Phase 23 — Infrastructure · Planned

Dockerfiles for web, api and worker. Docker Compose for local development:
Next.js, FastAPI, PostgreSQL, Redis, MinIO, Prometheus, Grafana. Terraform for
AWS: VPC, ALB, ECS/Fargate, RDS PostgreSQL, ElastiCache Redis, S3, CloudWatch,
IAM. Modular and configurable.

## Phase 24 — CI/CD · Planned

GitHub Actions: `ci.yml`, `test.yml`, `eval.yml`, `build.yml`, `deploy.yml`.
Pull request: lint, typecheck, unit tests, integration tests, security scan,
frontend tests, build. Main: all of the above plus the evaluation benchmark.
Deployment: build images, push to registry, deploy, smoke test, verify health,
roll back on failure where practical.

_Partially in place:_ `ci.yml` runs frontend and backend lint, typecheck, tests,
migration round trip, build and Playwright.

## Phase 25 — Documentation · Planned

README, `docs/PRD.md`, `docs/TDD.md`, `docs/architecture.md`,
`docs/evaluation.md`, `docs/threat-model.md`. ADRs for LangGraph, LlamaIndex,
PostgreSQL + pgvector, Redis queue, SSE, modular monolith, model routing and
cloud deployment. README covers problem, features, screenshots, architecture,
agent graph, data model, API architecture, evaluation methodology,
observability, security, load testing, deployment, local setup, production
setup, future improvements.

_Mostly in place:_ all documents and all eight required ADRs exist. Remaining:
screenshots, and refreshing every section once the system is complete.

---

## Implementation rules

1. Do not use mocked data once a real implementation exists.
2. Keep mock providers only for tests and local UI development.
3. Do not fabricate research results.
4. Never fabricate citations.
5. Never claim evaluation performance without executing evaluation.
6. Never claim load-test performance without measuring it.
7. Make external data providers replaceable interfaces.
8. Make LLM providers replaceable.
9. Make search providers replaceable.
10. Keep research workflows resumable.
11. Persist important state.
12. Put timeouts around external dependencies.
13. Bound every loop.
14. Bound every expensive operation.
15. Track every LLM call.
16. Track every tool call.
17. Treat web content as untrusted.
18. Use least-privilege tool access.
19. Prefer asynchronous I/O for network-bound work.
20. Prefer parallel execution when tasks are independent.

## Final acceptance criteria

A new user must be able to:

1. Register / log in — _Phase 20_
2. Create a research request — **done**
3. Select Quick or Deep Research — **done**
4. Submit a complex research question — **done**
5. Observe agent execution in real time — _stream done, agents Phase 9–10_
6. See sources being discovered — _UI done, discovery Phase 6_
7. See evidence being collected — _UI done, extraction Phase 11_
8. See contradictions — _UI done, detection Phase 11_
9. Wait for iterative research to complete — _Phase 9_
10. Receive a structured report — _UI done, generation Phase 12_
11. Open citations — _UI done, validation Phase 12_
12. Inspect sources — _UI done_
13. Inspect evidence supporting claims — _UI done_
14. View previous research — **done**
15. Run evaluations — _Phase 18_
16. View system / LLM metrics — _Phase 17_

The repository must also contain: automated tests, an evaluation suite, Docker
setup, CI/CD, observability, load-testing scripts, architecture documentation,
security documentation and deployment configuration.

At every stage prioritise correctness, maintainability, security, observability,
reproducibility and measurable performance over quantity of code.

## Reporting at the end of a phase

Provide: files created; architecture implemented; tests created; commands to run
it; remaining work; and any architectural decisions made. Then wait for the next
phase.
