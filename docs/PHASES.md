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
| 14  | Streaming                  | Done     |
| 15  | Caching                    | Done     |
| 16  | Cost and token governance  | Done     |
| 17  | Observability              | Done     |
| 18  | Evaluation framework       | Done     |
| 19  | Testing                    | Done     |
| 20  | Security                   | Done     |
| 21  | Load testing               | Done     |
| 22  | Optimization               | Done     |
| 23  | Infrastructure             | Done     |
| 24  | CI/CD                      | Done     |
| 25  | Documentation              | **Next** |

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

## Phase 14 — Streaming · **Done**

Server-Sent Events. Frontend receives `research_started`, `planner_started`,
`planner_completed`, `search_started`, `source_found`, `source_processed`,
`claim_extracted`, `verification_started`, `critic_started`,
`additional_research_requested`, `synthesis_started`, `report_completed`,
`research_failed`. Store important events server-side.

_Landed:_ a run narrates itself. The worker turns each of the graph's
supersteps into the event vocabulary the frontend has rendered since Phase 1,
Redis pub/sub carries it to whichever API replica is holding the stream, and
`research_events` keeps every event so a reconnect replays exactly. ADR 0018
records the decisions the rest of this rests on.

**The insert allocates the sequence, because nothing else can.** `seq` is the
`id:` a browser echoes back in `Last-Event-ID`, so it has to be unique and
monotonic per run across every process that emits - and from this phase the API
and the worker both do. A per-process counter hands two events one id and the
client silently skips one; a Redis counter has to expire, and a counter that
expires mid-run restarts at 1. So the append is one
`INSERT ... SELECT COALESCE(MAX(seq),0)+1 ... RETURNING seq` against a unique
`(run_id, seq)`, and a loser re-reads and retries. `next_seq` and `publish` as
two calls is exactly the shape that allocates a number in one place and uses it
in another, and it is gone: `publish(draft)` returns the numbered event.

**Everything emitted is observed, never predicted.** LangGraph reports a
superstep after it finishes, so every event is derived from state that already
contains the work it describes. A subtask is announced when its outcome exists;
a second round when the _plan_ for it exists, with the critic's own words as
the reason, rather than when the critic asks - the critic asking and the graph
looping are not the same fact. The cost is that an event lags its work by one
node. The benefit is that the stream never describes something that did not
happen, which is the same rule as everywhere else in this system.

**What has already been streamed is the cursor.** The emitter rebuilds what a
run has already announced from the run's own events, so a worker that takes
over a paused run continues the stream instead of re-announcing forty sources.
It needs no state of its own: the record of what a client was told _is_ the
record of what was told, and it survives a process replacement because the log
does.

**Redis carries events and never decides what they are.** The pub/sub channel
and its capped, expiring buffer are the fast path; `history` reads rows. Losing
all of Redis costs a client latency, not events - which is ADR 0005's "dispatch
mechanism, not source of truth" applied to the second thing Redis does.

Three graph values grew a field so the stream could describe real things rather
than placeholders: a `SourceRef` now carries its type, publisher and chunk
count, and a `TaskOutcome` carries the queries it actually issued. All
defaulted, so a checkpoint written before this phase still loads.

Migration 0010 adds `research_events`. 26 new tests, 1069 total.

Found while building and running it:

- **A `jsonb` payload does not come back in the order it went in.** The event
  a client parses is identical; the _bytes_ of the SSE frame are not, because
  the column stores an object rather than the text of one. A test that compared
  frames as strings failed, and the fix was the test: the contract is the
  parsed event, and a client that depended on key order would be broken by any
  JSON library.
- **A test that opened its own `Database` leaked a connection pool**, which
  surfaced as an unraisable `ResourceWarning` about a socket during teardown -
  a long way from the test that caused it. The suite already has a fixture that
  disposes one.
- **Waiting for "terminal or paused" races a run that is already paused.** The
  restart test's second worker was stopped before it had looked at the run,
  and the stream ended one event short. Phase 13 had already written that
  warning into `run_one`'s docstring; this is the second time it was needed.

## Phase 15 — Caching · **Done**

Redis caching for search results, URL content, embeddings and safe deterministic
transformations. Content hashes as keys. Invalidation rules. Never cache
sensitive per-user information globally. Deduplicate simultaneous identical
fetch/search operations.

_Landed:_ `app/cache` - a backend, a key scheme, a policy and a single-flight -
and four things behind it: a search provider's answer, a fetched page, the
article extracted from that page's HTML, and one text's embedding vector. ADR
0019 records why those four and not the obvious fifth.

**The namespace list is closed, and that is how the per-user rule is kept.**
There is nowhere to put a retrieved passage, a run's evidence or a report, so
none of them can be cached by accident. The one namespace that touches user
content is the embedding cache, keyed by the hash of the exact prepared text -
reading an entry requires already holding the text, so the cache cannot tell a
caller anything it did not bring with it - and `CACHE_EMBEDDINGS=false` exists
for a deployment that would rather not make that argument at all.

**Completions are not cached, and the switch is deliberately not thrown.**
"This prompt is deterministic" is a claim about a prompt that nothing here can
check, and a wrong hit on a synthesis is the most expensive mistake the system
could make. An embedding is deterministic by definition, which is why it is the
only model call that is cached - per text rather than per batch, so a document
sharing chunks with one already ingested sends only the new ones.

**Caching and deduplication are two promises, and only one is optional.**
Simultaneous identical calls are coalesced whether or not anything is stored:
the researchers of one run, given overlapping subtasks, ask for the same URL in
the same millisecond and all miss the cache. The scope is one process, which is
the scope the architecture produces - a run has exactly one worker (ADR 0017).

**A hit is still a recorded call.** `cache_hit` is on both ledgers now (the
`llm_calls` record was missing it), because a run whose ledger simply lacks a
call cannot be told from one that never made it, and Phase 18 has to be able to
report what caching saved rather than estimate it.

**A failure is a miss.** An unreachable backend, an entry written by an older
encoding, a value over the size ceiling: each degrades to computing the value
again. A research run must never fail because an optimisation was unavailable,
and the policy layer enforces that rather than trusting each backend to.

37 new tests, 1106 total. No cache-hit rate is reported yet: that is a
metric, and metrics are Phase 17.

Found while building and running it:

- **The fetcher makes two requests the first time it visits an origin.** A test
  counting sockets to prove the second fetch was served from cache counted
  robots.txt as well and read 2 where it expected 1. The politeness fetch has
  its own cache and its own lifetime; the test now counts pages.
- **A cached page has to be reconstructed, not revived.** `UntrustedText`
  sanitises on construction, so a decoder that trusted the stored string would
  be a way for retrieved content to enter the system without crossing the
  boundary that exists to clean it (ADR 0011). Both decoders go through the
  constructor, and a test round-trips a bidirectional override character to
  prove it.
- **An embedding key must be the _prepared_ text.** The registry's model has a
  task prefix, and a query and a passage differ only by it - so keying on the
  caller's raw text would serve a question the vector of a passage, which is
  the exact recall bug Phase 8 added the prefixes to avoid.

## Phase 16 — Cost and token governance · **Done**

Every LLM call records provider, model, prompt tokens, completion tokens, total
tokens, latency, estimated cost, research_id, agent and timestamp. Per-run
budgets. When a budget is exceeded, stop the workflow safely and return a
partial result.

_Landed:_ the trace tables are written, so "which step spent the money" is a
query rather than an investigation, and a run's ceiling is enforced before the
money is spent rather than noticed afterwards. `/activity` serves real rows for
the first time (FR-10).

**A node execution is the unit of the ledger.** One `agent_runs` row per visit
to a node, carrying its round and - for a researcher - the subtask it was
given; every tool call and model call that node makes hangs from it. The
current span travels in a `contextvars.ContextVar`, because a tool call is made
deep inside a researcher through a belt shared by the whole process, and
threading an id through every tool signature would be plumbing for a value that
is genuinely ambient. Each researcher runs in its own task, so each gets its own
copy.

**Recording never fails the work.** Every write is wrapped: a node whose ledger
row could not be written has still done its job, and a run that failed because
its telemetry did would be a much worse outcome. The two database recorders
wrap the logging ones rather than replacing them - a row is queryable and a log
line is greppable, and losing either would be a step backwards.

**The ceiling is enforced at the gateway, which is the only place it can be.**
The graph has checked the budget between nodes since Phase 9; a node that
starts under the ceiling may finish well over it, and a researcher fanned out
four ways can overshoot by four model calls before anything looks. A refused
call raises, the graph treats it as a _limit_ rather than as a broken agent, and
the run proceeds to synthesis with a caveat naming what stopped it.

**The step that writes the report is never refused, and that is the point.**
FR-8 says a run that hits a limit returns a partial result; the partial result
is a report, and a report costs a call. A guard that refused it would turn every
budget stop into a run with nothing to show - the exact outcome the requirement
exists to prevent. The overshoot is one call wide and the report says so itself.

**An unpriced model is refused under a budget**, which is what Phase 9's own
note in `app/agents/budget.py` pointed at: a run whose spend cannot be measured
cannot be held to a limit. It fails over to the next model in the chain, and
`REQUIRE_PRICED_MODELS=false` is there for a deployment that would rather run
unpriced models and accept an unenforceable ceiling. Every model in the shipped
registry is priced, the self-hosted ones explicitly at zero.

Migration 0011 makes two columns nullable. 30 new tests, 1136 total.

Found while building and running it:

- **The ledger could not say "not measured".** `llm_calls.cost_usd` and
  `agent_runs.cost_usd` were `NOT NULL DEFAULT 0`, and Phase 16 is the first
  code to write them. A model the registry does not price produces _no_ cost,
  which is not a cost of zero: recorded as `0`, an unpriced run reads as a free
  one and contradicts the caveat the graph attaches beside it. Both are nullable
  now. `tool_calls.cost_usd` is left alone - a tool call that costs nothing
  genuinely costs nothing.
- **A budget the guard remembered by itself gave a crashed run three budgets.**
  The first version kept a run's spend in the process and restored it on
  re-enrolment, which works only if the same process picks the run up again -
  and a resumed run is usually a different one, which has never seen the first
  attempt. The caller now supplies the starting point from the row it claimed,
  because the caller is the one holding the row.
- **The event sequence's retry count was a guess, and eight concurrent writers
  beat it.** Phase 14's append relied on a unique constraint and three retries,
  reasoned from "contention is one event wide". A test that emitted eight at
  once exhausted them. A transaction-scoped advisory lock keyed by the run
  makes the allocation atomic instead; the constraint and the retry stay as
  what would catch an emitter that skipped the lock.
- **The worker harness had no tracer, so the first end-to-end trace test found
  nothing.** Correct of the test and wrong of the harness: the trace is a
  deliverable of a run, and a harness that leaves it out tests a worker no
  deployment runs.

## Phase 17 — Observability · **Done**

OpenTelemetry tracing across HTTP request, research run, agent node, LLM call,
tool call, retrieval and database queries where practical. Prometheus metrics,
Grafana dashboards, structured JSON logs. Track research success/failure rate,
P50/P95/P99 latency, LLM latency, tool latency, tokens, cost, cache hit rate,
retrieval metrics, queue depth, active workers, database pool saturation.
Integrate LangSmith when configured; the system stays usable when it is not.

_Landed:_ both processes expose Prometheus metrics, every seam opens a span,
`trace_id` and `span_id` finally reach the rows that have carried the columns
since Phase 3, and `/evaluations/system` serves measured numbers instead of a
`501`. The Grafana dashboard is a file in `infra/monitoring`, so a panel is
reviewable rather than something someone clicked together.

**Tracing is configured or absent, never half-on.** With no exporter endpoint
the global no-op provider stays in place: every span in the code still runs,
costs nothing measurable, and records nothing. That is what keeps `if tracing
is enabled` out of the hot path entirely - there is no such check anywhere
outside `app/observability`. The OTel _API_ is a hard dependency for that
reason; the SDK and the exporter are imported inside `configure_tracing`, and
`import app.main` still costs the same six seconds it did before.

**A label is bounded or it is not a label.** The route template is rebuilt by
substituting the captured path parameters back into the path, so a run id
cannot reach a label unless the router never captured it, and an unmatched
path - which is attacker-controlled - is reported as `unmatched` without being
read. Provider, model, role, tool and status are closed vocabularies. What
belongs per run is in the ledger, which is a database and is built for it.

**Absence is reported as absence.** `/evaluations/system` returns `null` for a
window in which nothing finished, and `total_cost_usd` is `null` when _any_
call in the window was unpriced - one `COUNT(*) FILTER` away from a partial
sum presented as a total, which is the specific dishonesty this system is
built to avoid. A run whose start is unknown is counted but not timed: a
histogram with a false observation in it is worse than one with a gap.

**Telemetry cannot break the work it watches.** Every observation is wrapped,
and a test replaces a counter with one that raises to prove the call still
succeeds. The same rule the ledger writes follow, for the same reason.

**LangSmith is on only if asked, and is configured by construction.** Phase 9
turned it off because the library reads `LANGSMITH_TRACING` directly, past the
typed settings layer. The fix is not to set that variable from the settings -
that would leave two places the decision can be made, and would break the rule
that the settings layer is the only reader of the environment. The client is
built from the typed key and handed to `tracing_context`, so the decision
exists exactly once and an unset key means no client and no tracing.

Four dependencies added: `opentelemetry-api`, `opentelemetry-sdk`,
`opentelemetry-exporter-otlp-proto-http`, `prometheus-client`. 22 new tests,
1158 total.

Found while building and running it:

- **This FastAPI mounts its routers rather than flattening them**, so the
  matched route on the scope carries its _router-relative_ path:
  `/api/v1/research/{run_id}` arrived as `/research/{run_id}`, which would
  silently merge with any future `/api/v2` route of the same shape. The
  template is rebuilt from the path and its captured parameters instead -
  which also makes the no-run-ids-in-labels property structural rather than a
  consequence of how a library happens to name things.
- **Enabling LangSmith the obvious way broke a security test, correctly.**
  Setting the library's environment variables from the settings is what the
  library's own documentation suggests, and
  `test_settings_are_the_only_reader_of_the_environment` failed on it.
  Building the client explicitly is both the fix and the better design.
- **The default Prometheus registry is process-wide and raises on a duplicate
  name**, so a second application instance in one test session would bring the
  process down over telemetry. Every process builds its own registry.

## Phase 18 — Evaluation framework · **Done**

`data/eval/` dataset; each case has question, expected topics, expected source
types and expected claims where practical. An evaluation runner. Metrics —
Retrieval: Recall@K, Precision@K, MRR, NDCG. Generation: correctness,
groundedness, faithfulness, citation precision, citation recall. Agent: task
completion, tool selection accuracy, unnecessary tool calls, planning quality,
recovery rate. System: latency, cost, failure rate. Evaluation reports and
thresholds (e.g. citation correctness ≥ a configurable gate). **Never fabricate
benchmark results; display only measured values.**

_Landed:_ `make evaluate` runs a versioned dataset through the real research
graph, scores what came back, stores one row per case with the commit and the
thresholds in force, prints a report and exits non-zero when a gate regresses.
`/evaluations` serves it instead of a `501`.

**A case says what a good answer contains, never what it says.** The system is
non-deterministic by construction - a model writes the prose and a critic
decides how many rounds to run - so a golden text would measure agreement with
one past run. A case asserts topics the plan should cover, source types the
run should reach, and claims named by _normalised key_, which is exactly what
the claim normalizer exists to make stable across two differently worded
correct answers.

**Structural before semantic.** Whether a citation resolves - claim to
evidence to source - is arithmetic over the run's own data and is measured
here. Whether the evidence _supports_ the claim is a judgement that needs a
model, and conflating the two hides bugs inside model-quality noise: a
structural failure is a defect, not a weak model.

**Unmeasurable is `None`, in every layer.** The scorer returns it, the
aggregate skips it rather than averaging in a zero, the stored row omits the
key, the API reports `null`, the printed report says `not measured`, and the
gate cannot fail on it. That last one matters most: a gate that failed on
absence would go red on any partially labelled dataset and teach everyone to
ignore it. The dataset is what needs fixing, and the report names the metric.

**Every threshold ships ungated, and that is the honest state.** A gate
written before a baseline is an aspiration presented as a requirement. They
are configuration so they can be ratcheted upward from the first real
measurement, and they are stored _with_ each result so a later edit cannot
turn a past failure into a pass.

**The four case classes `docs/evaluation.md` names are all present**, and the
one that matters most is the question with no good public answer: the correct
behaviour there is a low-coverage report that says so, and a run that produces
confident claims is the failure being tested for.

25 new tests, 1183 total.

**No benchmark has been executed.** The suite is built and tested against
scripted agents; running it needs model credentials and spends real money on
every case, and no baseline is published that was not measured. That is the
rule this phase exists to enforce, so it applies to this phase first.

Found while building and running it:

- **The dataset directory resolved one level short.** `parents[3]` from
  `app/evaluations/dataset.py` is `apps/`, not the repository root -
  `app/core/config.py` walks four for the same depth. It failed loudly
  because the first test asserts the shipped dataset loads, which is the
  reason to have that test at all rather than only unit-testing the loader.
- **Making the store a base class instead of a Protocol broke structural
  typing**, quietly for ruff and loudly for mypy. Every other seam in this
  codebase is a Protocol; this one now is too.

## Phase 19 — Testing · **Done**

pytest, Vitest, Playwright, integration tests, agent workflow tests, retriever
tests, evaluation tests. Scenarios that must be covered: successful deep
research; search provider timeout; LLM timeout; LLM rate limit; worker restart;
duplicate URL; contradictory sources; no useful sources; empty search results;
budget exceeded; maximum iterations reached; malicious prompt injection in
source content; SSRF attempt; user cancellation; research resume after
interruption.

_Landed:_ `apps/api/tests/scenarios/`, where each of the fifteen situations has
at least one end-to-end test over the **whole vertical slice** - queue, worker, lease,
graph, all nine agents, the toolbelt with its SSRF guard and retry policy,
ingestion, retrieval, the evidence projection, report assembly, the citation
check, the event stream, the call ledger and real Postgres. `make
test-scenarios` runs them; they are part of `make api-test` too. Plus the user's
half of cancellation as a Playwright journey, which had no test at all.

**Exactly two things are replaced, and they are the two a test may not have.**
The model, by a provider that answers from a script - behind the _real_ gateway,
so routing, failover, retries and pricing run and a run's cost is still the sum
of what the registry says its calls cost. And the socket, by an `httpx2` mock
transport - behind the _real_ guarded client, so the SSRF guard, the redirect
policy, the size ceiling, robots and the search vendor's own request shaping and
response parsing all run. Everything between them is the system.

**Answers are chosen by schema and by prompt, never by call number.** The graph
runs researchers in parallel, so a script indexed by position answers whichever
subtask won the race. A rule keyed by the schema a call asked for is
deterministic however the calls interleave - and where a real model would have
read its input, the script reads it too: `quoting` finds the passage that
actually contains the quote rather than guessing at retrieval's ranking.

**The list of scenarios is code, not prose.** `catalogue.py` holds the fifteen,
every test claims its entry by decorator at import time, and `test_catalogue.py`
fails the build when the two disagree in either direction. A required scenario
that quietly lost its test is the failure mode this phase exists to prevent, and
a document cannot catch it.

Four defects, each of which only a whole-run test could find:

- **A run silently lost sources it had already fetched and parsed.** The
  filesystem object store checks that a key's resolved path is inside the
  artifact root, comparing against a root resolved once at construction. A
  researcher collects pages concurrently, so several uploads land in a run
  prefix that does not exist yet and each creates it - and on Windows a
  `resolve()` racing the creation of its own parent returns the
  extended-length form of the path, which is not "inside" the plain-form root.
  A measured reproduction refused one upload in seven. Nothing above it ever
  knew: the collector correctly treats a page it cannot store as one page's
  problem, so a run just came back with fewer sources than it gathered.
  `_path_for` now normalises both sides, which is not a relaxation - the
  resolution still happens and a path that genuinely escapes still fails - and
  `test_object_storage.py` holds both backends to it.
- **FR-7 was unreachable.** A claim's id was derived from its normalized key
  alone, and the contradiction checker looks for a key held by _more than one_
  claim - so two sources quoting different numbers about one subject collapsed
  onto a single id, the second silently replacing the first, and no key could
  ever be held by two claims. Every unit test of the checker passed, on state
  the normalizer cannot produce. A claim's identity is now what it asserts:
  key _and_ normalised value. Corroboration is unchanged - two sources agreeing
  on both halves are still one claim with two spans - and a round that rephrases
  a claim out of its value corroborates the one it re-found rather than sitting
  beside it.
- **Every document ingestion raised at the default log level.**
  `logging.makeRecord` refuses an `extra` field that shares a name with a
  `LogRecord` attribute, and three call sites passed `created`, which every
  record has. `log_level` defaults to `info`, so in any ordinary deployment the
  ingestion pipeline threw a `KeyError` after doing all its work; the collector
  catches per-page failures, so a worker would have gathered nothing and every
  run would have failed at synthesis, several layers from the cause. Uploads
  would have 500'd. Nothing saw it because the suite pins `log_level` to
  `warning` and a disabled logger never builds a record - which is the same
  reason Phase 10's `filename` collision hid. Fixed at all three sites, and
  `test_errors_and_logging.py` now scans every `extra={...}` literal in `app/`
  for the whole class. The scenario suite turns the `app` loggers up to INFO,
  because a suite claiming to run the system a deployment runs has to run its
  logging too.
- **The worker harness wrapped a real researcher in a helper meant for a
  scripted one**, seeding source rows the real collector had already written.
  Harmless by luck - the helper checks before inserting - and wrong the moment
  either side changed.

42 new tests, 1225 total. The whole suite is green; nothing here is skipped
except the pgvector and Redis tests that skip everywhere on this machine.

Two things the suite records rather than changes: `llm_calls.fell_back_from`
names a model by its _registry key_ while `model` is the vendor's model id, two
adjacent columns in two vocabularies; and a report's contradictions section is
written by the synthesizer, not assembled from rows as Evidence and References
are, so what the tests assert is that the writer is _given_ both sides inside
the delimited block.

## Phase 20 — Security · **Done**

Authentication, authorisation, per-user research access, rate limiting, request
validation, SSRF prevention, prompt injection defences, content sanitisation,
secret management, security headers, audit logs. Never expose API secrets to
Next.js client code. Dependency scanning; container image scanning if practical.

_Already in place before this phase:_ authorisation and per-user access (Phase
2), request validation (Phase 2), SSRF prevention (Phase 6), prompt-injection
defences and content sanitisation (Phases 6 and 10, ADRs 0011 and 0015), secret
handling and security headers (Phases 2–3).

_Landed:_ the three controls the threat model named and the code did not have -
**authentication**, **rate limiting** and an **audit log** - plus dependency
scanning in CI. ADR 0021 records the decisions.

**The session is a row; the cookie is a pointer to it.** Argon2id passwords at
RFC 9106's low-memory profile, a 256-bit CSPRNG token stored as a SHA-256, and
an `HttpOnly` cookie. Not a JWT, and the reason is FR-1: revoking a device has
to take effect on that device's next request, and a signed token either cannot
be revoked or is checked against a list on every request - at which point it is
a session with extra cryptography. Register, sign in, sign out, list sessions,
revoke one, revoke every other device; `/settings` became real at the same time,
since it was waiting for accounts. Every hash runs in a thread: one is ~130 ms
of deliberate CPU, and on the event loop that is 130 ms of stall for every other
request in the process. A wrong password and an unregistered address return the
same status, the same code, the same message and the same amount of time.

**The development identity survives, narrowed twice.** `X-Aether-User` is gated
by the `local`/`test` allowlist as before, and now also by
`DEV_IDENTITY_ENABLED`, which can close that gate further but never open it -
so the tests that exercise real sign-in switch it off without pretending to be
another environment, which would also swap the queue, the cache, the storage
backend and the rate-limit backend. One asymmetry is deliberate: a request
carrying a cookie that does _not_ resolve is refused even in development, rather
than falling through to the shared identity, which would make a revoked session
look like a working one.

**Rate limiting is a token bucket, and it is attached to the router.** A fixed
window of 60 a minute allows 60 at 00:59 and 60 more at 01:00; a bucket
separates burst from sustained rate. Three classes - reading, starting work, and
attempting a credential - so heavy reading cannot spend the allowance that
bounds brute force. Declared once on the whole `/api/v1` router, because a route
added later must be limited before anybody remembers to decorate it. The
credential endpoints draw on two buckets, one keyed by the client address and
one by the address being attempted: an address-only limit bounds one attacker
trying many accounts and misses many clients trying one account. Both are spent
before any password is verified. Evaluated in Redis in one Lua script, so refill
and spend cannot interleave across replicas. It **fails open** - a backend it
cannot reach allows the request and logs an error - which is a trade the threat
model records rather than a bug.

**The client address is resolved through _declared_ proxy hops.**
`X-Forwarded-For` is ignored entirely unless a deployment says how many trusted
hops append to it. A caller who can choose their own rate-limit bucket has no
limit, and one who can forge the address in the audit log has erased it.

**The audit log is append-only and outside the request's transaction.** Every
authentication event and every research mutation, with the actor, outcome,
address, user agent and the request id that joins the row to the logs of the
request that produced it. Reads are not audited - every request is already in
the access log. Its own transaction, because the most valuable rows are written
on paths that end in an exception; a failed write is logged and swallowed rather
than failing the request, for the same reason the limiter fails open.

Four defects, each found by running it:

- **A foreign key silently discarded the record of every sign-up.** The audit
  row was written in its own transaction while registration's user INSERT was
  still uncommitted in the request's, so `fk_audit_log_user_id_users` refused
  it - and the swallow-and-log policy meant the refusal appeared only as a log
  line. The key was wrong in the first place: this table records what happened,
  and what happened stays true after the row it names is gone. Both `user_id`
  and `resource_id` are now identifiers rather than references, which also means
  deleting an account no longer has to choose between cascading the trail away
  and nulling the actor.
- **Signing out did not sign anything out.** The account menu linked to
  `/login`. `useLogout` existed and nothing called it, so the server session
  stayed live and one person's cached research stayed in the browser for
  whoever used the machine next. The cache is now cleared on `onSettled`, not
  `onSuccess`: a sign-out that fails at the network still has to forget.
- **`Result` has no `rowcount`.** Four revocation paths returned "how many
  sessions did I revoke" through an attribute mypy could not see on the
  declared type - it lives on the DBAPI cursor result. One annotated helper
  rather than four casts.
- **A blank cookie setting stopped the process from starting.** `.env.example`
  ships every optional key present and blank, so `make env` produced
  `SESSION_COOKIE_SECURE=` - which pydantic cannot read as a boolean, so a
  process started from the shipped example refused to boot. Exactly Phase 13's
  blank-credential finding, and caught by the test that phase added for it.
  `SESSION_COOKIE_DOMAIN=` was the quieter half of the same bug: it parses, and
  an empty domain would have been emitted as a bare `Domain=` on every
  `Set-Cookie`. Both now resolve as "not configured".

**Dependency scanning** runs on both ecosystems in CI and as `make audit`:
`pip-audit` against the resolved Python environment, `npm audit` against the
lockfile. Reported rather than blocking - an advisory published overnight is not
a reason an unrelated change cannot merge. One finding is open and accepted and
named in the threat model: `nltk` (PYSEC-2026-3740) has no patched release,
arrives transitively through `llama-index-core`, and nothing here imports it.
Container image scanning waits for Phase 23, because there are no images yet and
scanning one that does not exist is a job that always passes.

On the frontend: a registration page, sign-out wired to the endpoint, and a
`RequireSession` guard that sends a signed-out visitor to `/login`. The guard is
a redirect, not a boundary - the session cookie is scoped to the API, which in a
real deployment is a different host, so Next.js middleware cannot read it. The
boundary is the API, which refuses every request without a session.

68 new API tests, 1293 total; 6 new frontend unit tests, 106 total; and two new
Playwright journeys, 21 total - one of which is sign-out, because that was the
half a unit test would have let through. Everything is green; nothing is skipped
except the pgvector and Redis tests that skip everywhere on this machine.

## Phase 21 — Load testing · **Done**

Locust or k6 scenarios at 10, 25, 50 and 100 concurrent research jobs. Measure
throughput, queue depth, completion rate, P50/P95/P99, database and Redis
utilisation, worker utilisation, LLM throttling. **Do not fake performance
numbers** — generate benchmark reports from actual tests.

_Landed:_ two load tests, because there are two ceilings, plus the instrument
that was missing to measure the third thing the phase asks for. The measured
report is [`docs/load-testing.md`](load-testing.md) and the raw artefact is
`data/loadtest/results.json`. **185 research runs were executed to produce
it.**

**The pipeline arm** (`make loadtest`) offers 10, 25, 50 and 100 jobs at once
to a real worker with four execution slots, against real Postgres, the real
queue and lease, the real graph, all nine real agents, the real toolbelt behind
its SSRF guard, real ingestion, real retrieval and the real projections. It
scripts the same two things the scenario suite scripts and for the same reason:
the model, behind the real gateway at a declared 0.5 s per call, and the socket,
behind the real guarded client. Provider latency is therefore an _input_,
printed beside every number it produced - a load test that made real calls would
measure a vendor's queue rather than this system's, cost hundreds of dollars a
profile, and not be reproducible.

**The surface arm** (`make loadtest-api-local`, or `make loadtest-api` against a
deployment) is Locust, driving the reads the frontend actually issues from
simulated users who each register their own account. Locust is fetched by
`uv run --with locust` rather than added to the lockfile, the arrangement
`make audit` already uses for `pip-audit`.

**LLM throttling needed an instrument that did not exist.** The gateway has
always held a concurrency semaphore, and a call that waited nine seconds behind
it was indistinguishable from a slow provider in every record this repository
keeps - `latency_ms` is the provider's own measure and starts once the slot is
held. `ConcurrencyLimiter` wraps the semaphore with the clock either side of the
acquire: same acquire, same fairness, plus `gateway.saturation` for a load test
and an `llm_slot_wait_seconds` histogram and `llm_calls_in_flight` gauge for
Grafana, which has a panel for them. Every acquisition is observed, the
immediate ones included, because a histogram fed only the waits reports a
healthy median while the system queues.

What the measurement says:

- **100% completion at every load.** 185 offered, 185 completed, none lost.
- **Throughput flattens at about 39 runs/min** on this machine at four slots.
  The rise from 22 is the ramp disappearing into the average, not a scaling
  gain.
- **The run does not get slower; the queue gets longer.** Execution P50 is
  6.6 s at ten jobs and 6.1 s at a hundred, and its P95 at a hundred is _lower_
  than at ten. Queue wait is what grows, linearly: 14.5 s to 75.8 s. That is
  the behaviour ADR 0001 was chosen for, stated as a measurement.
- **The worker's slots are the ceiling and nothing else is close.** Slots busy
  in 100% of samples at a hundred jobs; the database peaked at 5 connections of
  15 available and never touched overflow; **of 1,480 model calls, zero waited
  for a gateway slot** and peak in-flight was 4 of 8. Raising
  `llm_max_concurrent_calls` would do nothing - the lever is worker concurrency
  or a second worker.

**And it found a defect, which is what it is for.** Peak queue depth reached
**4,662 entries for 100 offered jobs**. The reconciliation sweep asks Postgres
which runs should be on the queue, and a run still `queued` because the worker
is busy answers yes on every sweep; neither the sweep nor the queue
deduplicates. Nothing runs twice - the claim is a conditional update and the
duplicate is dropped - but `queue_depth` is the metric on the dashboard and the
obvious signal for scaling workers, and it is wrong by a factor of 46; under
Redis the list grows without bound for as long as the backlog lasts. Every test
of the sweep passes and the scenario suite drives it fifteen ways, because none
of them holds a backlog open for longer than the grace period. It is fixed and
re-measured in Phase 22.

Two defects in the load test itself, both found by running it and both fixed
before it landed, because each would have produced a plausible wrong number:
the generated addresses used `.invalid`, which `email-validator` rejects as a
special-use domain, so every registration 422'd and - since the development
identity answers an unauthenticated request in `test` - the load silently ran as
one shared user against one shared rate-limit bucket, measuring contention no
deployment has; and it called `/auth/login` after `/auth/register`, which
already signs the account in, doubling the spend on the bucket that exists to
bound brute force. The run now closes the development identity outright, so a
broken sign-in fails loudly instead of measuring the wrong system.

32 new API tests, and the four declared loads are asserted against this document
so that dropping one fails the build.

## Phase 22 — Optimization · **Done**

Profile first. Then, based on measurements: parallel agent execution, async I/O,
connection pooling, Redis caching, search-result deduplication, embedding
caching, smaller models for simple tasks, model routing, context trimming,
source compression, retrieval filtering, bounded concurrency. Document
before/after metrics. **Do not optimize without measurement.**

_Landed:_ one defect fixed with a measured before and after, one capacity
relationship measured that previously had to be guessed, and two large numbers
deliberately left alone with the measurement that says why. Most of the list
above was already built - parallel agents (9), async I/O throughout, connection
pooling (3), Redis caching (15), search-result deduplication (11), embedding
caching (15), model routing (5), retrieval filtering (8), bounded concurrency
(5, 9, 13) - so this phase is what the rule at the end of the list actually
asks for: measure, then act only where the measurement points.

**Profiling started as a `GROUP BY`, not as a sampler.** Phase 16 already writes
an `agent_runs` row per node execution with its own `latency_ms`, so "which node
is expensive" is answerable exactly, over real runs, from rows the system wrote
while doing its job. `app/loadtest/breakdown.py` reads them back and the load
report prints them. Over 25 runs: the **researcher is 42% of all node time**,
and six of the remaining seven nodes cost 0.54 s each - which is the scripted
provider's 0.5 s plus about 40 ms of bookkeeping. There is nothing in those to
optimise that is not the provider.

A CPU profile came second, behind a flag (`--cprofile`), and its first lesson
was about itself: **cProfile counts every resumption of a coroutine as a call**,
so an async context manager reports several times the sessions actually opened.
Inferring database round trips from it produced a plausible wrong number.
Postgres was asked instead - `pg_stat_database.xact_commit`, two readings and a
subtraction - which gives **about 84 transactions per research run**, exactly.

**The defect Phase 21 found is fixed, and the fix is measured.** The
reconciliation sweep asked Postgres which runs should be on the queue and
re-enqueued them, with no memory of having just done so; a run still `queued`
because every slot was busy answered yes on every sweep. `last_queued_at`
(migration `0013_dispatch_clock`) gives the question its memory, and the rule is
one sentence: re-dispatch only if the run has never been dispatched, if
_something has happened to it since_ (`last_queued_at <= updated_at`), or if
that dispatch is old enough to be presumed lost. The middle clause is what keeps
a handed-back run, a due retry and an expired lease from waiting - each of those
writes `updated_at`, and none of them should queue behind a grace period.

Peak queue depth at 100 offered jobs: **4,662 → 224**, and 665 → 60 at fifty.
Completion stayed at 100% and throughput was unchanged within this machine's
±2 runs/min repeat variance. The residual is the repair mechanism doing its job:
from Postgres alone, "on the queue and not yet reached" and "the queue message
was lost" are the same observation, so the sweep still retries once per grace
period. The improvement factor is exactly the ratio of the grace period to the
sweep interval, which is what it should be.

One trap in that fix, caught by writing the test that fails without it:
`UpdatedAtMixin` declares an application-side `onupdate`, and **SQLAlchemy
applies it to a Core UPDATE as well as to an ORM flush** - so the stamp would
have bumped `updated_at` too, made it equal `last_queued_at`, and satisfied its
own rule on the very next sweep. The suppression would have been a no-op that
looked correct and passed every other test. `updated_at` is now pinned to itself
in that statement, with a test that fails if the pin is dropped.

**The capacity relationship is now measured rather than guessed.** At 50 offered
jobs with the provider held at 0.5 s per call, two repetitions per point:
`worker_concurrency` of 4 gives 33.5 runs/min, 8 gives 50.2, 16 gives 52.2. Four
to eight is +50%; eight to sixteen is +4%, and the reason it stops is in the same
rows - at sixteen, **12-15% of model calls began waiting for a gateway slot**
(the first time `llm_max_concurrent_calls` has bound anything in any measurement
here, and what Phase 21's instrument was built to see) and the database pool went
into its overflow. So raising `worker_concurrency` past 8 without also raising
`llm_max_concurrent_calls` and `db_pool_size` moves the bottleneck rather than
removing it.

The shipped default of 1 was **not changed**. That measurement is one throttled
laptop with a scripted provider, and the deployment model is horizontal
(ADR 0008), where one run per task makes resource accounting and autoscaling mean
something. A default changed on evidence this narrow would be the guesswork this
phase exists to replace. The relationship is recorded where an operator raising
the number will read it: beside the setting in `app/core/config.py`.

**Two things were deliberately not optimised**, because "do not optimize without
measurement" cuts both ways. 84 transactions per run is a lot, and is also under
4% of a run while the worker's slots are busy 99% of the time; batching the event
stream or deferring the ledger would buy a few percent and cost a progress stream
that is durable as it happens and an `/activity` view that shows a step while it
is still running. And the researcher's 42% has no hot spot inside it - the
largest identifiable non-database cost is `justext`'s stoplist construction at
about 46 ms per run, 0.75% of one. The honest conclusion from the profile is that
this system is not slow in any one place; it is bounded by how many runs a
process will execute at once, which is a capacity question rather than a code
one.

7 new API tests, 1332 total. `docs/load-testing.md` sections 7-9 carry the
numbers, `data/loadtest/results.json` is the current measurement and
`results-before-dispatch-clock.json` is the one it is compared against.

## Phase 23 — Infrastructure · **Done**

Dockerfiles for web, api and worker. Docker Compose for local development:
Next.js, FastAPI, PostgreSQL, Redis, MinIO, Prometheus, Grafana. Terraform for
AWS: VPC, ALB, ECS/Fargate, RDS PostgreSQL, ElastiCache Redis, S3, CloudWatch,
IAM. Modular and configurable.

_Landed:_ two images, a compose stack that runs the whole system, a modular
Terraform root that `terraform validate` accepts, the Kubernetes escape hatch
ADR 0008 promised, and 43 new API tests whose entire job is to notice when any
of it stops describing the code - 1375 total.

**Three files describe how to run this system and none of them can be run
here.** Docker is not installed on this machine and neither is an AWS account,
so the usual discipline - verify by running it - is unavailable for the first
time in twenty-three phases. Infrastructure that is never executed does not
fail; it drifts, silently, until somebody applies it. So the phase's test
strategy is different: `apps/api/tests/test_infrastructure.py` takes each fact
a deployment file states _about the application_ and asserts it against the
application. Every variable set in compose, Terraform or Kubernetes is a
declared `Settings` field - pydantic's `extra="ignore"` means a misspelling is
otherwise a silent default. Every probe path is a route the router serves - a
404 is an unhealthy replica, on every replica at once. The load balancer's idle
timeout outlasts `SSE_MAX_CONNECTION_SECONDS`; the worker's container stop
timeout outlasts `WORKER_SHUTDOWN_GRACE_SECONDS`; the worker's published
metrics port is the one the worker listens on; the API and the worker are one
image with two commands, in all three descriptions.

**The image's WORKDIR is load-bearing.** `app/core/config.py` computes
`REPO_ROOT` as `Path(__file__).resolve().parents[4]`, so the obvious
`WORKDIR /app` puts `config.py` four directories from the filesystem root and
that index raises `IndexError` - at import, before logging exists, with a
traceback about path arithmetic rather than about container layout. The images
keep the `apps/api` depth, and a test fails if the WORKDIR is ever shortened.

**Two other decisions that are the deployment, not decoration.** The built web
image carries a _relative_ API base URL, because the load balancer routes
`/api/*` to the API and everything else to the frontend: one origin, so the
request is same-origin, the session cookie is first-party, CORS never applies -
and the image contains no hostname, so one build serves every environment. And
`APP_ENV=production` is baked into the API image rather than left at the `local`
default, because under `local` the development identity is allowed: a deployment
that merely forgot the variable would serve every request as an authenticated
developer. Compose sets `APP_ENV=local` explicitly, which is a thing you can see
in a diff.

**The defect `terraform validate` found**, which review had not: Terraform
propagates sensitivity through every expression, so a map whose values are
sensitive is itself sensitive - keys included - and a sensitive value cannot
drive a `for_each`, because the key would appear in resource addresses and plan
output. The secrets module took one map of name to value and could not have been
applied. Names and values now arrive separately, with a precondition that keeps
the halves in step.

**What is deliberately not built.** The worker's autoscaling policy is CPU
rather than queue depth, in both ECS and Kubernetes, because
`research_queue_depth` is a Prometheus gauge and nothing publishes it to
CloudWatch or to a custom-metrics adapter - a target-tracking policy pointed at
a metric that does not exist would silently never fire, which is worse than not
having one. The shape of the policy is written down; it is switched off, and
says why at the code.

## Phase 24 — CI/CD · **Done**

GitHub Actions: `ci.yml`, `test.yml`, `eval.yml`, `build.yml`, `deploy.yml`.
Pull request: lint, typecheck, unit tests, integration tests, security scan,
frontend tests, build. Main: all of the above plus the evaluation benchmark.
Deployment: build images, push to registry, deploy, smoke test, verify health,
roll back on failure where practical.

_Landed:_ the five workflows, split by what they cost rather than by what they
cover - `ci.yml` (the fast gates plus one aggregate check), `test.yml` (the
suites and the services they need), `build.yml` (the two images: built, run,
scanned, then pushed by digest), `eval.yml` (the benchmark, guarded) and
`deploy.yml` (build, migrate, roll, prove, roll back). Plus `scripts/smoke.py`,
and 26 tests that hold the pipelines to the decisions they were written under.

**The split is by cost, not by subject.** A pull request runs one required
check, `ci`, which waits for lint, types, the secret scan, the dependency scan,
the infrastructure checks and the whole of `test.yml`. That aggregate job
exists because a branch protection rule names one check: without it, a job
added to `ci.yml` is optional until somebody remembers to add it to the rule as
well, and a test asserts it waits for every job beside it. The evaluation is
separate because each case is a real research run against real providers and
costs money; the images are separate because an image is an artifact rather
than a verdict, and a pull request that cannot merge until two images build is
a pull request waiting on the slowest possible thing.

**Four properties of the deployment pipeline are decisions rather than
defaults**, and each is asserted by `tests/test_workflows.py` rather than left
to review. The images are **scanned before they are pushed** - a vulnerable
image already in a registry is one somebody can deploy. Deployment is **by
digest**, because a tag can be moved after a deployment has decided to trust
it. The rollback target is **recorded before anything changes**, because after
a failure the only reliable account of what was running is the one written down
before it stopped. And the **migration runs before the services roll** and
after the new revision is registered.

**That last ordering was a defect until shellcheck and a second reading found
it.** The pre-deploy migration was written as a command override on the
existing task definition - but `ecs run-task` can override a container's
_command_ and not its _image_, so it would have run the previous build's
Alembic against the new build's schema requirement. The new revision is now
registered first, and the same revision both migrates and serves.

**The schema is deliberately not rolled back.** `alembic downgrade` against a
database that has already taken writes under the new schema can lose them, so a
failed deployment rolls the _code_ back and leaves the schema forward. What
makes that safe is a discipline rather than a mechanism: every migration must
leave the previous revision of the application able to run - expand in one
release, contract in a later one. It is written down at the step that depends
on it.

**The smoke test is the part that could be verified here, so it was.**
`scripts/smoke.py` is stdlib-only, because a smoke test that installs a
dependency tree first can fail for reasons that have nothing to do with the
deployment. It asks five questions from outside: does the frontend render, is
the API reachable **at the same origin** (which is what the built image's
relative base URL depends on), is it this application rather than something
that answered, does the versioned surface refuse an unauthenticated caller, and
are the probes still not routed publicly - `/ready` reports whether Postgres,
Redis and S3 are reachable, which is a free map of the deployment for anyone
who asks. `tests/test_smoke_script.py` runs the real application on a real
socket and runs those checks against it, in both directions: the
unauthenticated check is also run against a server with the development
identity on, and asserted to fail, because a check that passes either way is
not a check.

**Four defects the verification found**, every one of them only visible by
running something.

`gitleaks detect` does not exist any more - the subcommands were renamed, and
the pinned 8.30 binary has `git` and `dir` - so the secret scan would have
failed on its first execution rather than found anything. Run properly, it then
reported a leak in `.env.example`: a false positive, because the
`generic-api-key` rule matches the key `OPENAI_API_KEY=` and scores the entropy
of the comment on the following line. `.gitleaks.toml` skips that file, and a
new test walks every `SecretStr` field and fails if one of them loads a value
from the shipped example - which is what makes the allowance safe rather than a
hole. It also found the two MinIO defaults that are deliberately there, and
they are now pinned to that exact string rather than exempted by field.

`ecs run-task` can override a container's _command_ and not its _image_, so the
pre-deploy migration as first written would have run the previous build's
Alembic against the new build's schema requirement. The new task definition
revision is now registered before the migration, and the same revision both
migrates and serves.

The smoke test's probe check looked for a `"status"` key on both probes.
`/health` has one and `/ready` does not - readiness answers
`{"ready", "dependencies"}` - so the `/ready` branch was dead and a deployment
that published the state of every dependency would have passed the check. It
was invisible because `/health` failed first and hid it, and it was found by
reading the endpoint rather than the test. The check now recognises each path
by the shape of its own response and reports all of them together.

And the full suite found the fourth: a 30-second server-startup deadline in the
new test that passed every time the module ran alone and failed four-wide under
xdist, for the same reason every other timeout on this machine is generous.

**One rule declined rather than satisfied.** hadolint wants every OS package
version pinned (DL3008, DL3018). Each image installs exactly one - `tini`, so
that PID 1 reaps the parse worker's children - and pinning it means the build
breaks on the next security update of the one package we install, which is
backwards. `.hadolint.yaml` records the decision and where reproducibility
actually comes from: base images pinned by tag, dependency trees installed from
committed lockfiles. The threshold is set at `warning`, so everything else
still fails the build.

**What has not run.** `deploy.yml` and `eval.yml` have never executed - there
is no AWS account behind this repository and no provider credentials for the
benchmark - so both are descriptions rather than records, and both say so in
their own header.

What was verified, and how: `actionlint` with `shellcheck` on all five
workflows, `hadolint` on both Dockerfiles, `gitleaks` over the whole history,
and `terraform fmt` and `validate` on the root. None of those four tools is
installed on this machine - each was fetched as a pinned release binary into
the session's scratch directory, run once, and then wired into `ci.yml` so it
runs on every change from now on. Every one of them found something, which is
the argument for fetching them rather than assuming a check cannot be run
here. Plus 27 new API tests - 1402 total: `tests/test_workflows.py` for the
decisions the linters cannot see, `tests/test_smoke_script.py`, which runs the
real application on a real socket, and one in `tests/test_config.py` that is
what makes the secret scanner's single allowance safe.

## Phase 25 — Documentation · **Next**

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

1. Register / log in — **done**
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
