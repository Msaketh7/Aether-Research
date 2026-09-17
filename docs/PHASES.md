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
| 7   | Document ingestion         | Done     |
| 8   | Retrieval                  | Done     |
| 9   | LangGraph agent system     | Done     |
| 10  | Agents                     | Done     |
| 11  | Evidence system            | Done     |
| 12  | Report generation          | Done     |
| 13  | Background workers         | Done     |
| 14  | Streaming                  | **Next** |
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

## Phase 7 — Document ingestion · **Done**

Upload, parsing, metadata extraction, chunking, embedding, indexing — using
LlamaIndex where appropriate (ADR 0003). Support PDF, HTML, Markdown, TXT.
pgvector initially. Store document, chunk, embedding, metadata. Metadata
filtering.

_Landed:_ ADR 0012. `app/retrieval/` holds the pipeline, `POST`/`GET /files`
the upload surface, and `document_ids` on `POST /research` now attaches uploads
to a run. Uploads belong to users; each attached upload is ingested into the
run's own corpus as an ordinary `upload` source, so no provenance chain crosses
users.

The pipeline: the declared type checked against the bytes; parsing in a
killable child process (`pypdf`; the Phase 6 readability extractor for HTML, so
hidden markup is stripped for uploads too; strict decoding for Markdown and
text); language detection (`py3langid`, "unknown" below 0.80 confidence);
LlamaIndex chunking at 512/64 tokens with every chunk's offsets computed and
verified; then two writes. Source, document and chunks commit first, and the
vectors follow a batch per transaction, so a failed embedding call costs one
batch and the retry embeds only what is missing. Idempotent at every step,
including two deliveries of one job at the same moment (a transaction advisory
lock). `ChunkFilter` narrows a run's chunks by source type, format, language,
page range, section, publication date and embedding state, using the chunk
metadata's GIN index where it can.

The parser child inherits only an allowlist of operating-system variables (no
API keys, no database URL), has Python-level network access refused, is killed
at a deadline, and on POSIX runs under address-space and CPU ceilings. It is
the one module permitted to start a process: the security tests exempt it by
file, keep `eval`/`exec`/`pickle`/`shell=True` forbidden there too, and assert
its argv is fixed.

Migrations now form two branches: `core` (relational: 0003) and `vector` (needs
pgvector: 0002, 0004). Chaining relational work after 0002 would have made the
whole schema depend on the extension; as branches, the relational schema stays
buildable and testable without it. `alembic upgrade heads` applies both.

165 new tests, 577 total; the three that need pgvector skip locally and run in
CI. Verified on real input as well as in the suite: the
production path, isolated parser included, ingested this repository's TDD,
README and threat model (153 KB of Markdown with tables, code fences and deep
heading trees) into 147 chunks, every offset exact and none over 511 tokens.

Eight defects found while building and running it:

- The embedding column was `vector(1536)`, but the only declared embedding model
  produces 768 dimensions, so no vector could ever have been stored. Migration
  0004 resizes it, and the pipeline checks the model's width at startup.
- `documents.content_hash` was unique across the whole database. The same PDF
  in two users' runs collided, and a shared row would have let one user's
  deletion cascade into another's evidence. It is now unique per source.
- `POST /research` accepted `document_ids` and silently dropped them.
- `gateway.embed()` had no gateway timeout and no retry, and a failed call left
  no record. It now retries like generation and records every attempt. It never
  fails over, because vectors from two models are not comparable.
- Ollama truncates an over-long embedding input by default, which would store a
  vector for text the chunk no longer matches. `truncate: false` makes it an
  error.
- LlamaIndex places each chunk at the first match after the previous one. In
  repetitive text that piles chunks at the start and leaves the rest uncovered
  (caught by the coverage check on the unbroken-text test). Chunks are now
  placed by alignment against the window a consecutive chunk must fall in.
- LlamaIndex joins heading paths with "/", so a heading naming a directory came
  back as several levels (caught by ingesting the TDD). Section labels are now
  built from the headings themselves.
- A cursor that is valid base64 but not an id reached `UUID()` unguarded and
  returned a 500, on `GET /research` as well as the new `GET /files`.

Stated rather than implied: nothing ingests at runtime until the worker exists
(Phase 13). `GET /research/{id}/sources` stays empty until relevance and
credibility are measured, because the `Source` DTO would otherwise display the
schema's placeholder 0.50 as a score. The vector-writing path runs in CI only,
since pgvector is not installed on this machine. No embedding model has been run
live: Ollama is installed but has not pulled `nomic-embed-text`.

## Phase 8 — Retrieval · **Done**

Hybrid retrieval: dense vector search, BM25/lexical, metadata filtering, rank
fusion, reranking. A `Retriever` interface with `retrieve()`,
`retrieve_with_filters()`, `retrieve_hybrid()`. Benchmark the strategies. Do not
hard-code a chunk size without documenting the decision. Retrieval evaluation
tests.

_Landed:_ ADR 0013. `PostgresRetriever` behind the `Retriever` Protocol, two
arms run concurrently and fused by reciprocal rank, then reranked; the metrics
of evaluation §3.1 as pure functions; and a benchmark that produced the first
measured retrieval numbers in this repository.

The dense arm is pgvector cosine over chunks embedded by the same model, with
the query vector bound as text and cast twice so no codec is needed on the
driver, and `hnsw.ef_search` widened to cover the request. The lexical arm is
`ts_rank_cd` over the generated `tsv` column. Both narrow through one filter
builder — `_chunk_selection` — so a filter, and `user_id` ownership, cannot be
honoured by one path and ignored by another. A result carries each arm's
outcome and each chunk's per-arm rank, because "the lexical arm found this at
rank 1 and the dense arm never saw it" is the shape of a retrieval bug and is
invisible once scores are fused.

Reranking is MMR — a _diversity_ reranker, named for what it does. The top of a
fused list is full of near-duplicates by construction: chunks overlap by 64
tokens, filings restate themselves, one wire story gets reposted. The
cross-encoder the TDD describes solves a different problem, is a model this
repository cannot yet run or price, and drops in behind the same interface once
the benchmark can say whether it earns its latency.

92 new tests, 669 total; the five that need pgvector skip locally and run in CI.

**The first measured retrieval numbers** (`data/eval/retrieval/`, 14
hand-labelled questions over this repository's own docs, lexical arm only):

| chunk / overlap | chunks | recall@10 |   MRR | nDCG@10 |
| --------------- | -----: | --------: | ----: | ------: |
| 256 / 32        |    247 |     0.714 | 0.331 |   0.423 |
| 512 / 64        |    195 |     0.714 | 0.342 |   0.427 |
| 1024 / 128      |    189 |     0.714 | 0.298 |   0.395 |

So 512/64 stays, now for a measured reason rather than a documented guess — the
open question ADR 0012 deferred to this phase. The same four questions are
missed at every size, and all four are vocabulary mismatches: exactly what the
dense arm exists for. The baseline shows what hybrid retrieval has to buy
without yet being able to price it.

Four defects found while building and running it:

- **The lexical arm matched almost nothing.** `websearch_to_tsquery` — the
  obvious function, and what this used first — combines unquoted words with
  AND. Retrieval here is given questions, not keyword lists, so "inference
  pricing memory export" required one chunk to contain every one of those stems
  and returned zero rows. The terms are now OR-ed, over lexemes Postgres itself
  produced from the bound parameter.
- **The benchmark could not reproduce its own numbers.** Ties in `ts_rank_cd`
  are common and broke on `document_id`, which is generated per ingestion, so
  the same corpus ingested twice ranked differently and MRR moved between runs
  of one measurement. The tiebreak is now the source's canonical URL, a
  property of the document rather than of the row.
- **`nomic-embed-text` is asymmetric and nothing said so.** It is trained with a
  task prefix on every input, a different one for a stored passage and for a
  question asked of it. Omitting them fails nothing — it just puts queries
  slightly away from the documents they should match. The prefixes are now
  declared per model in the registry and applied by the gateway, so a caller
  embeds a query by saying it is a query.
- **A top-50 would have returned 40.** pgvector's `hnsw.ef_search` defaults to
  40, and a metadata filter is applied after the index scan, so the configured
  candidate count was never going to be met.

Stated rather than implied: no dense or hybrid quality number exists. No
embedding model has been run against a real corpus here, and pgvector cannot be
built on this machine, so those rows report _not measured_ with the reason. The
dense SQL is exercised in CI against the pgvector image with a deterministic
stand-in embedder — which proves the SQL, and makes no claim about quality.
Nothing calls the retriever at runtime until the agents exist (Phases 9-10).

## Phase 9 — LangGraph agent system · **Done**

LangGraph as the orchestration layer (ADR 0002). A typed `ResearchState` holding
`research_id`, `query`, `research_plan`, `subtasks`, `sources`, `claims`,
`evidence`, `contradictions`, `completed_tasks`, `failed_tasks`, `iteration`,
`token_usage`, `estimated_cost`, `critique`, `report`.

Graph: START → Planner → Research Fan-Out → Evidence Extraction → Claim
Normalization → Verification → Critic → Conditional Re-Plan → Synthesis →
Citation Validation → END. Researchers run in parallel where possible. **Bound
every loop**: `MAX_RESEARCH_ITERATIONS`, `MAX_SOURCES`, `MAX_SEARCH_QUERIES`,
`MAX_RUNTIME`, `MAX_ESTIMATED_COST`.

_Landed:_ ADR 0014. `app/agents/` holds the graph, and nothing in it pretends to
be an agent. `state.py` is `ResearchState` with the fields above and the
reducers that make parallel writes safe; `nodes.py` is one Protocol per node,
implemented in Phase 10; `budget.py` is loop control as pure functions;
`graph.py` is the topology and the wrapper around every node; `checkpoint.py`
configures LangGraph's Postgres checkpointer for this system; `runtime.py`
starts a run, or resumes it from its last checkpoint.

The graph: planner → researchers in parallel (one `Send` per dispatched subtask,
each carrying its share of the remaining budget) → evidence → claim
normalization → verification → contradiction check → critic → re-plan or
synthesis → citation validation, with at most one repair. A quick run skips
verification, the contradiction check and the critic, but keeps evidence and
claims, because a citation resolves through them.

The wrapper around each node is where the guarantees live, so a Phase 10 agent
cannot opt out: the cancel flag and the FR-8 ceilings at entry, a timeout around
the call (a researcher is also held to the run's remaining time), usage and the
run clock at exit. A reached ceiling ends discovery, not the run: the report is
still written, with a caveat built from measured values that the graph - not the
synthesizer - puts in it. Every loop ends: rounds, searches, sources, runtime,
cost, one citation repair, and a recursion limit derived from the run's shape as
a backstop. Runtime counts active time only, so a crashed worker's downtime is
not charged to the run.

Checkpoints are written after every node, into tables migration 0005 creates
from a frozen copy of the library's DDL - a test fails when an upgrade changes
it - and read back through a serializer that revives only the types a state can
hold, derived from the state's own annotations. Cancellation reads the run's
status column, the one `POST /research/{id}/cancel` already writes.

107 new tests, 776 total: 768 pass, and the eight that skip locally are the
same eight that skipped before this phase. The checkpointer tests run against
real Postgres.

Found while building and running it:

- **LangGraph's checkpoint serializer constructs any class a checkpoint names**,
  logging a warning. A checkpoint is a table row, so whoever can write the row
  chooses what a worker constructs. Deserialization is now allowlisted.
- **An allowlist fails silently.** A stored model whose class is not on it comes
  back as a plain `dict`, with only a log line, and one that no longer validates
  is rebuilt without validation. A probe showed this before the state was
  written, which is why the allowlist is derived rather than hand-kept and a test
  round-trips every type.
- **The Postgres checkpointer read the run's stop reason back as a string.** It
  stores top-level `str`, `int`, `float` and `bool` values inline as JSON,
  subclasses included, so a `StrEnum` lost its type - only on Postgres, since the
  in-memory saver serializes everything. The graph compares
  `stop is StopReason.CANCELLED` by identity, so a cancelled run resumed from
  Postgres would not have been recognised by its routing; only the database
  probe inside each node would still have stopped it spending. Enums now live
  inside models, and a test fails on any top-level field that would lose its
  type.
- **The checkpointer migrates its own schema**, including three
  `CREATE INDEX CONCURRENTLY` statements that cannot run in a transaction. Alembic
  now owns those tables, and the worker never calls `setup()`.
- **LangGraph's default recursion limit of 25 ends a four-round deep run with an
  exception.** The limit is derived per run.
- **`max_search_queries` was the one FR-8 ceiling not frozen on a run**, so a
  deployment could change it under a queued run. It is now part of `RunLimits` in
  Python, the TypeScript contract and the mock fixtures; migration 0006 backfills
  existing runs, checked on real rows in both directions.
- **A pytest-asyncio loop-factory hook must answer for every test.** Returning
  nothing for tests that did not need a selector loop broke collection of the
  whole suite, so the hook is scoped to `tests/checkpointer/` by directory.
- **Phase 8 shipped five files that failed `npm run format:check`**, which CI
  runs: `registry.yaml`, `CLAUDE.md`, ADR 0013, the retrieval dataset README and
  this file. They are formatted, and CLAUDE.md now says to run the check.

Stated rather than implied: nothing runs the graph yet. Every node is a Protocol
until Phase 10 implements it, and the worker that would pick a run up is Phase
13\. Cost and search ceilings are enforced from what nodes report, so a node that
raises loses its usage from the total until Phase 16 reconciles against the
model-call ledger. LangSmith tracing is forced off. `langgraph-sdk` pins
`websockets` to 16.x.

## Phase 10 — Agents · **Done**

`PlannerAgent`, `WebResearchAgent`, `DocumentResearchAgent`, `DataResearchAgent`,
`EvidenceAgent`, `VerificationAgent`, `CriticAgent`, `SynthesisAgent`,
`CitationValidator`. Each with explicit instructions, limited tools, structured
input/output, timeout, retry policy, observability and unit tests. Agents
communicate through structured state, not unstructured string blobs.

_Landed:_ ADR 0015. Every Protocol Phase 9 declared now has an implementation,
and a run goes from a question to a validated report through the real graph.

**A model never emits an identifier.** It is shown a numbered catalogue built
from the state (`catalog.py`) and answers with numbers, which the agent maps
back. A number out of range is a fabrication the agent can see and drop; a
fabricated UUID is indistinguishable from a real one and would reach the report
as a citation to a source that was never retrieved. The schemas a model fills in
(`outputs.py`) are separate from the state schemas and contain no UUID field.

**A quote is verified before it becomes evidence.** The extractor names a
passage and quotes it; the agent finds the quote in that passage character for
character and computes the span's offsets from where it was found. A quote that
is not there is dropped and counted - a paraphrase has no offsets, an invention
has no source. `EvidenceItem` now refuses a span whose offsets do not bracket
exactly its text, so it is an invariant of the value rather than a rule an
extractor remembers.

**Identity is derived.** A claim's id is `uuid5` over the run and its normalized
key, a contradiction's over its ordered pair, so the same assertion found in a
second round is the same claim with more evidence behind it, and the same
disagreement found twice is recorded once.

**The citation validator makes no model call.** TDD 4.2 allows "deterministic
checks plus one LLM repair pass"; the repair pass is the graph re-running the
synthesizer, and the check is arithmetic over the state - marker to claim, claim
to evidence, evidence to a source the run really retrieved. Its usage is
genuinely zero. For the same reason the synthesizer may not write the
`references` or `evidence` sections: a model writing a list of sources is the
most reliable way to fabricate one.

Prompts are versioned Markdown files shipped inside the wheel
(`app/agents/prompts/`), one per agent step, with the system instruction fixed
and only the user turn templated. The three researchers sit behind a router that
resolves the channel the planner proposed against what the deployment has.

155 new tests, 931 total. The eight that skip locally are the same eight that
skipped before this phase: they need pgvector, which this machine's PostgreSQL
does not have, and CI runs them.

Found while building and running it:

- **Alembic's `fileConfig` switched off every application logger.**
  `disable_existing_loggers` defaults to `True`, and `migrations/env.py` imports
  the application to reach its metadata - so every `app.*` logger existed by the
  time Alembic configured logging, and every one was disabled. The migrations
  kept logging normally, which is what hid it. Harmless while migrations run in
  their own process; not harmless in a worker that migrates and then serves, and
  it is why no test in this repository could assert on a log line. Found by a
  test that could not see a line it had just written.
- **An error's context could break the handler reporting it**, which the fix
  above then made visible. `logging.makeRecord` refuses an `extra` field sharing
  a name with a `LogRecord` attribute, and the error handler spreads an
  `AppError`'s context into `extra` - so an upload refused for a format
  mismatch, whose context carries `filename`, raised a `KeyError` and turned its
  own 415 into a 500. Live in production the whole time, and invisible in the
  suite because the logger it raised in was disabled. `log_context` now prefixes
  a colliding key, and the reserved set is derived from a real `LogRecord`
  rather than listed, so a future Python cannot reintroduce it.
- **A finished `Completion` could not be priced by its caller.** The gateway
  computes cost for the ledger and hands the completion back without it, so an
  agent reporting what it spent would have had to reach past the gateway into
  the registry - the thing ADR 0007 exists to prevent. `LLMGateway.cost_of` and
  `ModelRegistry.find` close it; an unpriced model still reports an uncosted
  call rather than zero.
- **A researcher would have been routed as if every run were deep.**
  `SubtaskAssignment` carried the domains and the date range but not the mode,
  so a quick run's researchers would have spent deep-run prices. The mode is now
  part of the assignment.
- **One graph node runs every subtask**, so it has to know which researcher to
  be. `Subtask` gains a `channel` the planner proposes and the router resolves,
  narrowing only: a channel this deployment lacks, or documents for a run with
  no attached corpus, goes to the web and the change is logged with both.
- **The planner could not know whether the run had documents attached.**
  `ResearchRun` counts sources, not attachments, so the flag is now part of
  `RunParameters` and set by whoever builds the brief (Phase 13).
- **A source found again in a later round is not read again.** Sources merge by
  id keeping the first researcher's task key, so a later round's extractor sees
  only what that round newly attributed to it. That is the intended saving - the
  page has already been quoted from - and it means a round that finds only pages
  the run already had produces no new evidence, which the trace says.
- **The contradiction check makes no model call when no two claims share a
  key**, because a key held by one claim cannot disagree with anything. The
  node still runs, with its budget and cancel checks; it costs nothing.
- **Two defects in this phase's own code, found by re-reading rather than by a
  failure.** Evidence extraction interleaved the per-subtask chunk lists with
  `zip`, which stops at the shortest - so one subtask that retrieved a single
  chunk capped every other subtask at one and the rest were dropped with no
  trace. And claim normalization filtered a capped evidence catalogue rather
  than capping the filtered one, so once a run held more evidence than a prompt
  carries, a new span could fall outside the cap and never become a claim. Both
  now have tests, checked against the old code first.

Stated rather than implied: nothing runs these agents in production yet - the
worker is Phase 13, and a created run still stays `queued`. No live model call
has been made on this machine (Ollama is installed but not running), so the
agents are exercised against a scripted provider behind the real gateway, which
is what prices them. The `parse` tool still has no caller through the toolbelt:
ingestion calls the same readability extractor beneath it, inside the isolated
parser, so a fetched page is parsed once rather than twice. No end-to-end
evaluation has run; that is Phase 18.

## Phase 11 — Evidence system · **Done**

Entities: `Source`, `Claim`, `Evidence`, `Citation`, `Contradiction`. Every claim
linkable to evidence; every evidence record identifies its source; every source
carries title, url, publisher, `published_at`, `accessed_at`, `content_hash`,
`source_type`. Implement claim extraction, evidence extraction, claim
normalization, source deduplication and contradiction detection. **Do not
silently resolve conflicting information — surface conflicts in the report.**

_Landed:_ ADR 0016. The chain the previous phase derived in memory is now durable and
readable. `GET /research/{id}/sources` and `/evidence` serve real rows for the
first time — paginated, filtered, and scoped by a join rather than by a filter
applied after fetching.

**A checkpoint is not a read model.** LangGraph stores a run's state as one
serialised blob per step, addressed by thread id and readable only by the graph.
That is right for resuming a run and useless for every other question — which
claims rest on this source, which contradictions are unresolved, show me page
two. `EvidenceProjector` writes the state onto the relational entities Phase 3
declared, and the graph runner calls it after every invocation, including on the
way out of a failure: a run that died at synthesis still found sources and quoted
spans, and someone diagnosing it needs to see them.

**Projecting twice writes the same rows twice.** A resumed run re-projects
everything its checkpoint holds, so every id is derived from what it describes —
a claim's from the run and its normalized key, a span's from the claim and the
span, a contradiction's from its ordered pair — and every write is an upsert. Two
columns are deliberately not overwritten: a claim's `first_seen_at`, because a
re-projection is not a new belief, and a contradiction's `resolution`, because
the projection only ever writes `unresolved` and overwriting would undo a
person's judgement. FR-7 cuts both ways.

**Corroboration counts clusters, not rows.** A wire story republished by four
outlets is one source's word four times, and counting it as four tells a reader
something false in the direction they are least likely to check. Deduplication
(`app/evidence/dedup.py`) groups a run's sources by identical content hash, then
by canonical URL, then by overlapping text, and a claim's `corroboration_count`
is the number of distinct clusters behind it. Every threshold is set to merge
reluctantly: a false merge costs a claim corroboration it deserved, a false split
invents corroboration that never existed, and only the second misleads.

**Credibility is a declared table, not a model.** `app/sources/credibility.py`
scores the _origin_ — restricted-registration domains, mastheads with a
correction policy, user-generated platforms, and everything else as `unknown`,
which sits below a forum because "we do not know who runs this" is worse than
"we know, and it is a forum". Four tiers, the tier is the score, every number a
constant with a reason beside it. A learned reputation score would be a number
nobody could account for attached to a citation.

**A claim now carries what it asserts.** The normalized key holds
`subject | predicate | qualifier` and deliberately not the value, which is what
lets two sources that disagree share a key. So a contradiction had no way to say
what the disagreement was _about_. The normalizer is asked for the value and the
agent checks it against the claim's own text, exactly as a quote is checked
before it becomes evidence: found, or dropped. `claims/v2`.

37 new tests, 968 total. The eight that skip locally are the same eight as
before: they need pgvector, and CI runs them.

Found while building and running it:

- **The sources endpoint would have failed on its first real row.** Ingestion
  wrote the fetch facts — status code, redirect count, whether robots.txt was
  consulted — into `sources.credibility_metadata`, which the `Source` DTO parses
  as a `SourceCredibility` that forbids unknown fields. Nothing had noticed
  because the endpoint returned an empty list. The fetch facts belong on the
  document, beside the format and the parser, and now live there.
- **The relevance placeholder.** `relevance_score` was `NOT NULL DEFAULT 0.50`
  and nothing had ever computed it, which is why Phase 7 left the endpoint empty
  rather than render "relevance 50%" as a measurement. The column, the DTO and
  the TypeScript type are nullable now, and `null` means not measured — the web
  app's formatter already renders that as a dash. No relevance is measured in
  this phase; saying so is the point.
- **One span can be evidence for two claims.** `evidence.claim_id` is a single
  foreign key, so the table's grain is the link rather than the span, and the row
  id is derived from both. Projecting a span cited by two claims as one row would
  have silently dropped one of them.
- **Jaccard was the wrong measure for near-duplicates**, found by running the
  test rather than by reasoning about it. What is compared is the stored 280-
  character excerpt, so a reprint carrying a byline pushes the end of the other
  copy's text out of the window; Jaccard counts that against both texts and a
  genuine reprint scored 0.82, under any threshold high enough to be safe.
  Containment normalised by the shorter text asks the question actually being
  asked — is one of these inside the other — and scores it 1.0.
- **A pair matched by two rules was reported as the weaker one.** Two copies with
  the same digest also have overlapping text, so the cluster came back as a
  judgement when it was a certainty. The rules now run strongest first and a
  merge records only the rule that actually joined two components; a cluster is
  reported as the weakest rule that made a merge _in it_, which is the honest
  reading of a group held together partly by a digest and partly by a judgement.
- **The upserts set an `updated_at` that does not exist.** These tables carry
  `created_at` only, and the column was not added: when a projection last
  rewrote a row says nothing about the run it describes.
- **The projection's own summary counted what state held rather than what it
  wrote**, found by re-reading rather than by a failure. Both row builders drop
  what they cannot write — a span the checkpoint no longer carries, a
  contradiction whose claims are gone — so the line a worker logs would have
  claimed more evidence than exists. It counts the rows now.
- **The attached-upload descriptor carried a placeholder credibility dict**, with
  a comment saying credibility belonged to Phase 11. It does now: an upload is
  primary and unrated, because its origin is the person who asked and nothing in
  the system has assessed it.

Stated rather than implied: nothing runs a graph in production yet, so nothing
has been projected outside the tests — the worker is Phase 13. Deduplication
compares the stored excerpt, which catches a syndicated copy and does not catch a
paraphrase; nothing here claims otherwise. `claims.task_id` stays NULL because
the graph does not write `research_tasks` rows, and `task_external_id` carries
the link to the subtask that found a claim's first span. Relevance is not
measured, and `citations` is Phase 12's table.

## Phase 12 — Report generation · **Done**

Structured report: Executive Summary, Key Findings, Detailed Analysis,
Competitive Landscape, Evidence, Contradictions, Risks, Opportunities,
Conclusion, References. Pydantic models for the schema. Citations by source id.
A citation validator that verifies the source exists, the source was actually
retrieved, the claim is linked to evidence, the evidence belongs to the source,
and the citation is not fabricated.

_Landed:_ the chain from a sentence in a report to the passage it rests on is
complete and stored. `GET /research/{id}/report` serves a real report with its
sections, every resolved citation, and the citation check's verdict.

**Section vocabulary, stated rather than quietly resolved.** The list above is
the original brief's. PRD FR-9 refined it - Risks and Opportunities became
Confidence Assessment and Recommendations, and Conclusion was folded into the
Executive Summary - and the `ReportSectionKind` enum, TDD 7.2, the shared
TypeScript types and the frontend have all implemented FR-9's nine since Phase 1.
This phase follows FR-9. Widening the vocabulary to twelve would mean a
migration, a contract change and three more sections for a distinction the PRD
deliberately removed; if the brief's list is the intended one, that is a decision
to take deliberately rather than by inference from a heading.

**`[n]` changes meaning here, and that is the point.** The synthesizer cites
claims by catalogue number, because a model that emits identifiers can fabricate
them (ADR 0015). A reader needs the other thing: a marker that resolves to a
_source_ and the quote behind the sentence. So `app/reports/assembly.py` resolves
every marker through the same catalogue the synthesizer was shown, then renumbers
it to a citation ordinal in first-appearance order. A marker that resolves to
nothing becomes `[0]` - an ordinal no citation can hold, so the reader sees a
visibly broken citation. Leaving the original number would be worse than
deleting it: `[7]` in a report with seven citations quietly becomes someone
else's source.

**Evidence and References are assembled from rows.** The synthesizer has been
refused those two sections since Phase 10; this is what fills them. Evidence
lists every citation with its verbatim span, References lists each cited source
once with the markers pointing at it - one entry per source, because a list that
named the same page twice would overstate how many sources the report rests on.

**A citation is a row with foreign keys.** `citations.claim_id` and
`citations.source_id` are `ON DELETE RESTRICT`, so a citation to a claim that
does not exist cannot be inserted at all - the database enforcing what the
product promises. That is why the report and the evidence chain are projected
together in one transaction (`app/research/recorder.py`): the claims a report
cites must be written before its citations, and a report that fails to write must
not leave the claims behind as a run that half finished.

**Verbatim text is escaped on its way into a section.** A fetched page containing
"[3]" embedded in the Evidence section would render as a citation pointing at
whichever source is third. The renderer now takes a backslash escape for exactly
this sequence, and everything untrusted goes through `escape_markdown`.

29 new tests, 997 total. The eight that skip locally are the same eight as
before: they need pgvector, and CI runs them.

Found while building and running it:

- **A migration id longer than 32 characters cannot be applied.** Alembic's
  `alembic_version.version_num` is `varchar(32)`, and `0008_report_confidence_unmeasured`
  is 33. Every test that provisions a database failed at once, on the `UPDATE
alembic_version` rather than on anything in the migration, which is a long way
  from the cause. The revision is `0008_report_validation`.
- **The report page told the reader that rejected citations had been "removed
  from the report".** They are not removed - they survive as `[0]` markers,
  which is the honest behaviour and the opposite of what the sentence said. The
  copy now says what happens.
- **The Markdown renderer had no escape at all**, found by reading it while
  designing the Evidence section rather than by a failure. Every inline sequence
  it recognises is cosmetic except `[n]`, which is a claim about provenance, so
  that is the one that got an escape.
- **`reports.overall_confidence` was `NOT NULL DEFAULT 0.50`** and is the mean
  confidence of the claims a report cites. A report that cites none has no such
  mean, and the placeholder would have been read as a measurement in the one
  case where the number matters most. Nullable now, like
  `sources.relevance_score` in 0007 and for the same reason.
- **The validator's reason codes would have reached the reader as jargon**, found
  by looking at the rendered page rather than at the JSON: the summary above the
  prose would have said "no_such_claim (2)". The code is what is stored, and the
  API translates it into a sentence on the way out.
- **The projection had nowhere to put the verdict.** How many citations were
  checked and why the rest failed lived only in the graph's checkpoint, which no
  request can read, and only the counts are recoverable from the stored text -
  the reasons are not. `reports.validation` holds it.

Stated rather than implied: nothing runs a graph in production yet, so no report
has been projected outside the tests - the worker is Phase 13, and a created run
still stays `queued`. No live model call has been made on this machine. The
frontend still runs against its fixtures by default; pointing it at the live API
is one environment variable, and there is nothing there to read until the worker
exists.

## Phase 13 — Background workers · **Done**

Redis-based workers: API → Redis queue → worker → LangGraph → PostgreSQL. Worker
processes separate from API processes. Job statuses: queued, running, paused,
completed, failed, cancelled. Retries, idempotency, persisted workflow
state/checkpoints. **Research must survive worker restarts.**

_Landed:_ a run no longer stays `queued`. `python -m app.workers.runner` claims
it, ingests the documents it was created with, runs the graph, and leaves behind
the claims, evidence, contradictions and report every earlier phase could only
write in a test. ADR 0017 records the decision the rest of this rests on.

**The lease is the run's own row, and the queue is only a doorbell.** ADR 0005
had already said Redis is the dispatch mechanism rather than the source of truth;
this is what that means in code. Taking a run is one conditional `UPDATE`, so a
duplicate delivery, a user's cancellation and a second worker offered the same
job are all settled by the database rather than by a lock. Every later write the
worker makes names its own `worker_id` and excludes terminal statuses, which is
why a stalled worker cannot overwrite its successor - or a cancellation. Migration
0009 adds the four columns: `worker_id`, `heartbeat_at`, `attempts`,
`next_attempt_at`.

**"Running" is not a status, so the row follows the graph instead.** The
vocabulary has `planning`, `researching`, `verifying`, `synthesizing` and
`validating`, and a worker that set one of them at the start and nothing until
the end would be lying for most of the run. So the runner streams LangGraph
rather than awaiting it, and each superstep ends in one write that renews the
lease and records the phase, the progress and the run's live counts. The
progress bar is openly an estimate - a run's length is not known in advance,
because the critic decides whether there is another round - but it never goes
backwards, and a test walks a four-round trace to prove it.

**An attempt is spent when a run is claimed, not when one fails.** That is what
bounds a run that kills its worker: a process that dies mid-node refunds nothing,
so a poison run stops after `WORKER_MAX_ATTEMPTS` instead of forever. The mirror
of it is that a worker _asked_ to stop hands the run back as `paused` and returns
the attempt, because a deploy is not a failed attempt. Three deploys in a row
would otherwise exhaust a run that never went wrong.

**One thing is designed for rather than observed, and is marked as such.** A
blocking `BLPOP` must not outlast the Redis client's socket timeout, or every
quiet poll raises a read timeout indistinguishable from an outage. There is no
Redis on this machine, so that was reasoned about rather than hit: `build_redis`
takes the socket timeout as a parameter, the worker sizes it from its poll
interval, and the test that would catch a regression runs in CI against a real
server rather than against a fake that could not reproduce it.

**`paused` finally means what the frontend has always rendered.** A retryable
failure pauses with a backoff held in the run's row - a retry Redis forgets is a
run that never resumes - and the reconciliation sweep re-dispatches it when it
comes due, along with runs still queued long after creation and runs whose worker
stopped renewing its lease. Every worker sweeps without coordinating, because
re-dispatching a run that is already running is harmless by construction.

39 new tests, 1036 total, including a worker restarted across two real Postgres
checkpointer pools. The Redis tests need a server: CI now runs one and they skip
locally with a reason, the same arrangement as the pgvector tests - eleven tests
skip on this machine now rather than eight.

Found while building and running it:

- **Every UUID the ORM returned was asyncpg's subclass of `uuid.UUID`, not
  `uuid.UUID`.** It passes every isinstance check and every Pydantic field, so it
  travelled from a row into the graph's state unnoticed - and then LangGraph's
  checkpoint serializer, whose allowlist is derived from the state's _declared_
  types, refused to reconstruct it. The value came back as nothing and the
  resumed run failed a long way from the cause. Exactly the failure mode
  `app/agents/checkpoint.py` warns about, and invisible until a worker actually
  restarted. Fixed at the boundary that created it: `NormalisedUUID` in
  `app/db/base.py`, with a test that fails if it is removed.
- **Waiting for a run to reach a status races the worker.** A status written from
  outside - a cancellation - is true while the job is still in flight, so a test
  that stopped there cut the run off mid-node and asserted on half of it. The
  worker reports whether it is busy, and the tests wait for both.
- **A paused run rendered as a red failure.** `paused` has meant "resumable, and
  not a failure" since Phase 1, and this phase is what first makes it reachable -
  with the reason it stopped attached, which the run header showed in the same
  danger alert a failed run gets. It says "Paused, and will resume" in a warning
  now. Nothing else about the contract changed: `attempts` and `next_attempt_at`
  are the worker's business and are not exposed.
- **A process started from the shipped `.env.example` crashed at startup.** Every
  key in it is present and blank, so `make env` produces `OPENAI_API_KEY=` -
  which reads as `SecretStr("")`, not as an absent credential. A provider was
  built with it and the vendor SDK refused the empty key in its constructor,
  three layers below the typed settings that were supposed to prevent exactly
  this. A blank credential is now an absent one, and a test starts a process from
  the shipped example. Phase 5's defect, found by being the second process to
  read the configuration.
- **The scripted agents invent source ids.** A real researcher ingests a page and
  then reports the id of the row it has just written; the evidence projection's
  foreign key to `documents` is what noticed the difference. The test harness now
  stores what a researcher would have stored, which is also a statement of the
  contract the real one keeps.

Stated rather than implied: the worker publishes no progress events yet. The
event broker is in-process (Phase 2), so a worker publishing to it would reach
nobody; the Redis pub/sub broker and real emission are Phase 14, and until then
`/events` relays only what the API itself publishes. No live model call has been
made on this machine, so every run executed so far has been over scripted agents.

## Phase 14 — Streaming · **Next**

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
5. Observe agent execution in real time — _stream done, agents done, worker emission Phase 14_
6. See sources being discovered — _UI done, discovery done, live updates Phase 14_
7. See evidence being collected — **done** _(no live stream until Phase 14)_
8. See contradictions — **done**
9. Wait for iterative research to complete — **done**
10. Receive a structured report — **done**
11. Open citations — **done**
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
