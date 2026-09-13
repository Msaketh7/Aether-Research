# Aether Research: Technical Design Document (TDD)

|                     |                                                                                                    |
| ------------------- | -------------------------------------------------------------------------------------------------- |
| **Working name**    | Aether Research                                                                                    |
| **Document status** | Draft                                                                                              |
| **Version**         | 1.0                                                                                                |
| **Last updated**    | 2026-09-10                                                                                         |
| **Owner**           | Engineering                                                                                        |
| **Related**         | [`PRD.md`](PRD.md), `CHANGELOG.md`, `architecture.md`, `threat-model.md`, `evaluation.md`, `ADRs/` |

---

## Document control

> **In plain terms:** this file (`docs/TDD.md`) is the **single master**: always
> the current truth. It is versioned only when a real architecture or system
> design decision changes, not for wording, formatting, or added guidance. Those
> edits just update this file in place.

- **Master (this file):** living document, always current. Rendered to
  `docs/TDD.docx`.
- **What gets a new version (decisions only):**
  1. an architecture decision (a new or changed ADR always counts),
  2. a technical/system design decision: the agent graph, the data schema, an
     API contract, a hard limit or budget, a security control, the queue /
     durability / concurrency model, the observability or evaluation design,
     CI/CD, or the deployment topology,
  3. a decision that resolves or reopens one of the open technical questions.
- **What does NOT get a version:** wording and typography, plain-language
  rewrites, added captions / annotations / diagrams for readability, "how to
  read" / guidance sections, and the document tooling itself. These edits update
  the master (and `TDD.docx`) directly, with only the `Last updated` line
  touched.
- **When a version IS cut:** bump `Version`, add a Change Log entry (Added /
  Changed / Deferred / Replaced / Removed, each with a reason), regenerate
  `TDD.docx`, and write the snapshot pair `docs/versions/TDD-v<x.y>.md` + `.docx`.
- **Combined index:** `docs/CHANGELOG.md` lists every decision version of the PRD
  and TDD.
- **Versioning:** `MAJOR.MINOR`. MINOR = an additive or clarifying design change
  within the same direction (a new ADR is at least MINOR). MAJOR = a change in
  architecture direction, a removed capability, or an incompatible schema/API
  restructure.

### Version history

| Version | Date       | Status      | Summary                                   |
| ------- | ---------- | ----------- | ----------------------------------------- |
| 1.0     | 2026-09-05 | **Current** | Initial TDD from the staged specification |

### Change log

Newest first. Only decision versions are listed here.

#### v1.0 (2026-09-05): Initial TDD

- **Added**: first full TDD from the staged specification: design principles;
  system topology and the two deployment units (API + Worker); component design
  (Next.js frontend and its pages, FastAPI gateway, auth, research API, file
  API, Redis, task worker); the agent graph with per-node responsibilities,
  `ResearchState`, and loop control; the framework split (LlamaIndex /
  LangGraph / LangChain); the LLM Gateway and model routing; the full data
  architecture including a designed PostgreSQL schema for ~20 tables
  (`users`, `sessions`, `research_projects`, `research_runs`, `research_tasks`,
  `sources`, `documents`, `document_chunks`, `claims`, `evidence`,
  `contradictions`, `reports`, `report_sections`, `citations`, `agent_runs`,
  `tool_calls`, `llm_calls`, `evaluations`, `feedback`); the RAG pipeline; the
  web-content pipeline and deduplication; the async execution model and SSE;
  durability / resumability via LangGraph checkpoints; failure handling and the
  error taxonomy; the caching strategy; concurrency and backpressure caps;
  security architecture; observability; the evaluation system; the API surface;
  testing strategy; CI/CD; deployment and infrastructure; the repository
  structure; nine ADRs; and open technical questions.
- **Added (`sessions` table)**: beyond the table list supplied in the spec, a
  `sessions` table was introduced for server-side session storage.
  _Reason:_ FR-1 requires session management and per-device revoke.
- **Deferred (still open):** splitting the worker into dedicated ingestion /
  evaluation worker pools ("only at scale"); a dedicated vector database
  separate from Postgres (`ADR-0003`, revisit at scale); a standalone
  knowledge-graph store (kept as a projection of `claims` for now); Kafka for
  the queue (Redis + Celery/ARQ for v1).
  _Reason:_ avoid premature infrastructure; the v1 choices are sufficient at
  demo scale.
- **Open technical questions (unresolved):** embedding model + dimension and
  reranker choice; web-search vendor; Celery vs ARQ; pgvector index type
  (HNSW vs IVFFlat); whether the knowledge graph gets a dedicated store later;
  per-role LLM provider assignment pending measured cost/latency.

> Editorial and formatting revisions since v1.0 (a plain-language layer for
> non-technical readers, this document-control section, and a styling cleanup)
> are maintained in place and are **not** separately versioned.

---

## How to read this document

This is the **technical** design. It is still meant to be _followable_ by a
non-technical reader who wants to understand the architecture and the system
design.

- **Every major section opens with an indented "In plain terms" paragraph.** A
  non-technical reader can read Section 0, then the "In plain terms" box of each section,
  and come away with an accurate mental model.
- **The detail under each box is for engineers**: schemas, protocols, limits,
  trade-offs.
- **Jargon** is expanded in Section 25 (Glossary). The [`PRD.md`](PRD.md) glossary is
  gentler if this one is too dense.

---

## 0. The whole system in plain English

**The job.** Turn one hard research question into a trustworthy, fully-sourced
report, and do it like a real product, reliably, safely, within a budget, and
measurably.

**The shape of the system.** Think of a building with a **front counter** and a
**back room**:

- The **front counter** (the API) takes your request, checks who you are, and
  immediately hands you a ticket. It never makes you wait at the counter while
  the whole research job runs.
- The **back room** (a worker process) picks up tickets from a queue and does the
  slow work: running the team of AI agents, searching, reading, checking,
  writing.
- A **master filing cabinet** (the PostgreSQL database) holds the authoritative
  record of everything: your account, your research runs, every source, every
  claim, every citation.
- Inside that same cabinet is a **search-by-meaning index** (pgvector) so the
  system can find relevant passages even when they use different words.
- A **warehouse** (S3 object storage) holds bulky files (PDFs, saved web pages,
  finished reports), because those don't belong in a filing cabinet.
- A **fast front desk** (Redis) runs the ticket queue, remembers recent results
  so work isn't repeated, and acts as a bouncer that limits how much runs at
  once.

**Why it's built to "save its progress."** A deep research run takes minutes and
makes dozens of external calls. Servers restart. Networks blip. So after every
step the system writes a **save-point**. If anything interrupts it, it resumes
from the last save-point instead of starting over and re-spending money.

**Why three frameworks instead of one.** They do different jobs and are kept from
overlapping:

- **LlamaIndex** is the _librarian_: it reads documents and finds the relevant
  passages.
- **LangGraph** is the _project manager_: it runs the multi-step workflow, the
  loop, the save-points, and the live progress feed.
- **LangChain** is the _universal adapter_: one common way to call any AI
  provider or tool, so we can swap them without rewriting the agents.

**Why there's a "gateway" in front of the AI.** If 50 research runs each want to
call an AI model at once, that's 50 simultaneous calls and a surprise bill. The
**LLM Gateway** is a single chokepoint that limits concurrency (e.g. 8 at a
time), retries failures, falls back to another provider, tracks cost, and
refuses to blow the per-run budget.

**Why it grades itself.** A fixed set of 100-300 test questions with known-good
answers is run after every change. If quality drops below a threshold, the change
is blocked automatically.

The rest of this document is the detail behind each of those ideas.

---

## 1. Overview

This document describes the technical design of Aether Research: a long-running,
stateful, multi-agent research workflow. It covers system topology, component
design, the agent graph, the framework split (LlamaIndex / LangGraph /
LangChain), the LLM Gateway, the data model, the RAG and web-content pipelines,
the async execution model, durability, failure handling, caching, concurrency,
security, observability, the evaluation system, the API surface, testing, CI/CD,
and deployment.

### 1.1 Design principles

1. **The HTTP request never runs the research.** Work is enqueued and executed by
   a separate worker process.
2. **State is persisted, not held in memory.** Postgres is the system of record;
   LangGraph checkpoints make runs resumable.
3. **Every claim is traceable** to an evidence span and a source.
4. **Frameworks do not compete**: each owns exactly one layer.
5. **Bounded everything**: iterations, sources, cost, runtime, concurrency,
   search budget.
6. **Retrieved content is untrusted data.**
7. **Modular monolith**, deployed as two containers (API + Worker); split only at
   scale.

---

## 2. System topology

> **In plain terms:** this diagram is the "building". Traffic comes in from the
> internet, hits a load balancer (a traffic cop that spreads requests across
> servers), reaches the website, then the front-counter API. The API drops long
> jobs onto the Redis queue. The back-room worker runs the agent team. Results
> are filed in Postgres, indexed for meaning-search by pgvector, and bulky files
> go to S3.

```
                              INTERNET
                                 │
                         ┌───────▼────────┐
                         │ Load Balancer  │   spreads traffic across servers
                         └───────┬────────┘
                                 │
                      ┌──────────▼──────────┐
                      │  Next.js frontend   │   apps/web   (the website)
                      └──────────┬──────────┘
                                 │  HTTPS + SSE  (SSE = live progress feed)
                      ┌──────────▼──────────┐
                      │  FastAPI gateway    │   apps/api   (the front counter)
                      └──────────┬──────────┘
                ┌────────────────┼────────────────┐
                ▼                ▼                ▼
           Auth Service     Research API      File API
           (login/logout)   (start/read)      (uploads)
                                 │
                           ┌─────▼─────┐
                           │  Redis    │  queue + cache + rate limit + locks
                           └─────┬─────┘
                                 │
                          ┌──────▼───────┐
                          │ Task Worker  │  the back room: runs the research graph
                          │  LangGraph   │
                          └──────┬───────┘
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
          Planner           Researchers          Critic
                          ┌────┼────┐
                          ▼    ▼    ▼
                     Web  SEC  arXiv/GitHub
                                 │
                           Evidence Layer
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
          PostgreSQL        pgvector           S3 object store
        (master records)  (meaning-search    (PDFs, saved pages,
                           index, in PG)      reports)
                                 │
                          Synthesis Agent
                                 │
                          Citation Validator
                                 │
                             Report → PostgreSQL
```

### 2.1 Deployment units

> **In plain terms:** we ship two programs from one codebase (the front
> counter and the back room) so we can add back-room capacity independently as
> research demand grows.

| Unit                 | Contents                                                     | Scales on             |
| -------------------- | ------------------------------------------------------------ | --------------------- |
| **API container**    | FastAPI gateway, auth, research API, file API, SSE endpoints | request rate          |
| **Worker container** | Queue consumer + LangGraph runtime + agents + retrieval      | active research count |
| Postgres             | system of record + pgvector                                  | data volume / IOPS    |
| Redis                | queue, cache, rate limiter, distributed locks                | job throughput        |
| S3                   | large artifacts                                              | storage               |

At scale the worker splits into: research workers, ingestion workers, evaluation
workers, independently scaled.

---

## 3. Component design

> **In plain terms:** this section walks through each box in the diagram and says
> what it does and what it's built with.

### 3.1 Frontend (`apps/web`)

_Plain terms: the website you interact with, plus the live progress feed._

- **Stack:** Next.js (App Router), TypeScript, Tailwind, shadcn/ui, TanStack
  Query, native `EventSource` for SSE.
- **Auth:** Auth.js; middleware verifies the session on every protected route.
- **Data:** TanStack Query for REST calls; a dedicated SSE hook for the live
  activity stream, reconciled into the query cache.
- **Pages:**

  | Route                     | Purpose                                                  |
  | ------------------------- | -------------------------------------------------------- |
  | `/login`                  | Sign in                                                  |
  | `/dashboard`              | Recent research, quick-start box                         |
  | `/research/new`           | Question + mode + depth + domains + date range + uploads |
  | `/research/[id]`          | Run overview + live activity feed                        |
  | `/research/[id]/sources`  | Discovered sources, duplicate clusters, credibility      |
  | `/research/[id]/evidence` | Claims, supporting quotes, confidence, contradictions    |
  | `/research/[id]/activity` | Full step-by-step agent trace                            |
  | `/research/[id]/report`   | Final report with inline `[n]` citations                 |
  | `/settings`               | Profile, sessions, provider/model preferences            |
  | `/evaluations`            | Quality + system dashboards, benchmark history           |

- **Activity feed** renders a checklist driven by SSE events:
  `✓ Planning`, `✓ Searching 14 sources`, `→ Verifying claims`,
  `○ Writing report`.

### 3.2 API Gateway (`apps/api/app/api`)

_Plain terms: the front counter. Receives every request, checks it, and routes
it. Hands back a ticket for long jobs._

- **FastAPI**, modular monolith. Routers: `auth`, `research`, `files`,
  `evaluations`, `sse`, `health`.
- Request validation via Pydantic models shared through `packages/shared-types`.
- Every research endpoint enforces ownership (`research.user_id == session.user`).
- Rate-limiting middleware (Redis token bucket) keyed by user + route + a cost
  weight.
- Returns `202 Accepted` + `job_id` for research creation; never blocks on the
  workflow.

### 3.3 Auth service (`apps/api/app/auth`)

_Plain terms: the part that knows who you are and makes sure you only see your
own research._

- Auth.js on the frontend; the API validates the session and loads the user.
- Server-side session store in Postgres; session list + revoke.
- Authorization helper `require_owner(resource)` used by every research route.
- Roles: `user`, `admin` (admin only for eval-dataset management + ops views).

### 3.4 Research API (`apps/api/app/research`)

_Plain terms: start a research run, check its status, ask a follow-up, or cancel
it._

- `POST /research`, validate → create `research_projects` (if new) +
  `research_runs` row (`status = queued`) → enqueue job → return `202` +
  `run_id`.
- `GET /research/{id}`, run status, progress summary, links to sub-resources.
- `GET /research/{id}/events`, SSE stream (see Section 10).
- `POST /research/{id}/followup`, creates a child run with `parent_run_id` set.
- `POST /research/{id}/cancel`, cooperative cancel via a Redis flag the graph
  checks between steps.

### 3.5 File API (`apps/api/app/api` + `retrieval`)

_Plain terms: upload a document and it is checked and kept. When a research run
that names it starts, the system reads it, cleans it, cuts it into pieces,
indexes them, and adds it to that run's research material._

- `POST /files`, with the raw file as the request body: `Content-Type` is its
  type and `Content-Disposition` its name. The size ceiling is enforced while
  the body is read, and the bytes are checked against the declared type before
  anything is stored. PDF, HTML, Markdown and plain text are accepted. Returns
  `201`, or `200` with the existing record when the same user uploads the same
  bytes again.
- `GET /files` and `GET /files/{id}` list and fetch the caller's uploads.
- `POST /research` accepts up to 10 upload ids in `document_ids`. They are
  checked against the caller's uploads and recorded with the run; when the run
  executes, each is ingested into that run's corpus as a `source_type = upload`
  source (Section 8.1).
- Changed from the original presigned-S3 design (ADR 0012). At a 25 MiB ceiling
  the API can stream the body with a hard cap, the type check happens before
  storage rather than after, and the filesystem storage backend cannot presign.
  Presigning remains the path if files outgrow the proxy.

### 3.6 Redis

_Plain terms: the fast front desk: the ticket queue, the "we already did
this" cache, the bouncer, and a shared scratchpad._

| Use               | Mechanism                                                            |
| ----------------- | -------------------------------------------------------------------- |
| Job queue         | Celery or ARQ over Redis                                             |
| Cache             | search cache, URL cache, embedding cache, LLM cache (see Section 13) |
| Rate limiting     | token bucket per user/route/cost                                     |
| Distributed locks | ingestion dedup, single-writer per run                               |
| Temporary state   | SSE fan-out channel per run, cancel flags                            |
| Deduplication     | in-flight request de-dupe for the LLM Gateway and the fetcher        |

### 3.7 Task Worker (`apps/api/app/workers`)

_Plain terms: the back room. Pulls a job, runs the whole agent workflow, saves
progress after every step, and streams updates to your browser._

- Consumes jobs; for each research job it builds the LangGraph app with a
  Postgres checkpointer and invokes it with the run's thread id.
- Emits progress events to the run's Redis channel after each node.
- Honors the cancel flag and the hard limits (`max_iterations`, `max_sources`,
  `max_cost`, `max_runtime`).
- On crash/redeploy, a supervisor re-enqueues incomplete runs; the graph resumes
  from the last checkpoint.

---

## 4. Agent architecture (`apps/api/app/agents`)

> **In plain terms:** this is the "team of analysts" as a flowchart. Work starts
> at the top. The Planner splits the question. Researchers run **in parallel**.
> Their findings become claims-with-proof, get checked, and get cross-examined
> for contradictions. The Critic decides "enough?", if not, back around the loop
> (max 4 times). Then the report is written and every citation is validated
> before anything is saved.

### 4.1 Graph

```
                        START
                          │
                          ▼
                     Planner Agent ──────────────┐
                          │                      │ (re-plan loop,
                          ▼                      │  bounded ≤ 4)
                  Task Decomposition             │
                          │                      │
           ┌──────────────┼──────────────┐       │
           ▼              ▼              ▼        │
      Researcher 1   Researcher 2   Researcher N  │  ← parallel (LangGraph Send API)
           │              │              │        │
           └──────────────┼──────────────┘        │
                          ▼                       │
                  Evidence Extractor              │
                          │                       │
                          ▼                       │
                  Claim Normalization             │
                          │                       │
                          ▼                       │
                  Verification Agent              │
                          │                       │
                          ▼                       │
                  Contradiction Check             │
                          │                       │
                    ┌─────┴─────┐                 │
                    ▼           ▼                 │
                 Missing     Sufficient           │
                    │           │                 │
                    └───────────┼─────────────────┘
                                ▼
                          Synthesizer
                                │
                                ▼
                        Citation Validator
                                │
                          ┌─────┴─────┐
                          ▼           ▼
                     invalid cites  all valid
                          │           │
                     (repair once)    ▼
                          │       Final Report → persist
                          └───────────┘
```

### 4.2 Node responsibilities

_Plain terms: each row is one AI worker, what it takes in, what it produces, and
how powerful a model it needs._

| Node                                   | Input                                                                   | Output                                                                           | Model tier                                                |
| -------------------------------------- | ----------------------------------------------------------------------- | -------------------------------------------------------------------------------- | --------------------------------------------------------- |
| **Planner**                            | question, mode, depth, domains, date range, parent evidence (followups) | `ResearchPlan` (Pydantic): `research_goal`, `subtasks[{id, question, priority}]` | strong                                                    |
| **Researcher** (per subtask, parallel) | one subtask + `SearchBudget` slice                                      | ranked `RetrievedSource[]` + fetched `Document[]`                                | strong for reasoning; small for relevance filtering       |
| **Evidence Extractor**                 | documents for a subtask                                                 | `EvidenceCandidate[]` (claim text, span, offsets, stance)                        | medium                                                    |
| **Claim Normalization**                | evidence candidates                                                     | `Claim[]` with `normalized_key`, subject/predicate/object                        | medium                                                    |
| **Verification Agent**                 | claims + all evidence                                                   | per-claim `status` + calibrated `confidence`                                     | strong                                                    |
| **Contradiction Check**                | claims grouped by `normalized_key`                                      | `Contradiction[]` with `likely_reason`                                           | strong                                                    |
| **Critic**                             | coverage/confidence/contradiction state per subtask                     | `sufficient: bool` + `missing: MissingInfo[]`                                    | strong                                                    |
| **Synthesizer**                        | verified claims, contradictions, sources                                | report sections (Markdown) + citation markers                                    | strongest                                                 |
| **Citation Validator**                 | draft report + citation markers                                         | validated report or repair instructions                                          | small/medium (deterministic checks + one LLM repair pass) |

### 4.3 Graph state

_Plain terms: the shared "clipboard" every worker reads from and writes to. It is
saved after every step._

```python
class ResearchState(TypedDict):
    run_id: str
    question: str
    mode: Literal["quick", "deep", "conversational"]
    depth: int
    domains: list[str]
    date_range: tuple[date | None, date | None]
    parent_run_id: str | None

    plan: ResearchPlan | None
    subtask_results: Annotated[list[SubtaskResult], operator.add]
    claims: Annotated[list[Claim], merge_claims]
    evidence: Annotated[list[Evidence], operator.add]
    contradictions: list[Contradiction]

    iteration: int
    budget_spent: BudgetLedger            # cost, tokens, sources, elapsed
    critic_verdict: CriticVerdict | None

    report_sections: list[ReportSection] | None
    citation_report: CitationValidationReport | None
    status: str
    errors: list[NodeError]
```

- **Parallelism:** the planner node returns LangGraph `Send` objects, one per
  subtask, fanning out to a pool of researcher executions that reduce back via
  `operator.add` / a custom claim-merge reducer.
- **Quick mode** uses a trimmed graph: Planner → Researcher(s) → Synthesizer →
  Citation Validator (no verification/contradiction/critic loop).

### 4.4 Loop control

_Plain terms: the guardrails checked between every step so the job can never run
forever or overspend._

```
if iteration > max_iterations          -> force route to Synthesizer (with caveat)
if budget_spent.sources >= max_sources -> stop discovery, allow synthesis
if budget_spent.cost_usd >= max_cost   -> abort discovery, synthesize what exists
if elapsed >= max_runtime              -> abort discovery, synthesize what exists
if cancel_flag(run_id)                 -> checkpoint + mark cancelled
```

---

## 5. Framework responsibilities

> **In plain terms:** three libraries, three clearly separated jobs, librarian,
> project manager, universal adapter. They are deliberately not allowed to
> overlap, so each stays replaceable.

| Framework      | Layer                                          | Responsibilities                                                                                                                                                                   |
| -------------- | ---------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **LlamaIndex** | Data / Retrieval (_librarian_)                 | document loaders, parsing, chunking, indexing, hybrid retrieval (vector + BM25), metadata filters, reranking, per-document query engines, structured (Pydantic) extraction outputs |
| **LangGraph**  | Agent / Workflow (_project manager_)           | graph definition, state, routing, parallel `Send` fan-out, loops, checkpointing/persistence, streaming, human-in-the-loop, durable execution / resume                              |
| **LangChain**  | Model / Tool integration (_universal adapter_) | `LLMProvider` adapters, tool interfaces, prompt templates, output parsers, retriever interfaces shared by agents                                                                   |

ADR: `docs/ADRs/0001-framework-split.md`.

---

## 6. LLM Gateway & model routing (`apps/api/app/models`)

> **In plain terms:** every call to an AI model goes through one door. That door
> limits how many calls run at once, retries failures, switches providers if one
> is down, tracks the bill, and stops a run from exceeding its budget. It also
> sends easy tasks to cheap models and only hard reasoning to expensive ones.

### 6.1 Model registry

Agents never instantiate a provider. They ask the gateway for a **role**, and it
resolves the model:

```python
completion = await gateway.generate(
    role=AgentName.RESEARCHER, mode=ResearchMode.DEEP, prompt=prompt
)
```

Declared models live in `apps/api/app/models/registry.yaml`, one entry per model
carrying its provider, tier, context window, output ceiling, capability flags and
a **dated, sourced** price. A model whose price cannot be verified is declared
unpriced, and its cost is then reported as `null` rather than `0.00` — _not
measured_ and _free_ are different facts, and §16's budget ledger acts on the
difference.

Capabilities are declared, not assumed. The ones that already bite: current
Anthropic models reject `temperature`; Anthropic has no embeddings API; neither
OpenAI nor Ollama can count a prompt's tokens before generating; and an
embeddings model must never be selected as a chat fallback.

The role → tier policy lives in `app/models/routing.py` and follows ADR 0007:

```
planner, claim_normalizer                         small
researcher, evidence_extractor, citation_validator medium
verifier, critic                                   strong
synthesizer                                        strongest configured
```

Research mode shifts that: `deep` uses the base tier, `quick` and
`conversational` step down one, and the synthesizer never drops below `strong`.

_(An earlier draft of this section routed the planner to `strong`. ADR 0007 and
the build plan both put it on the cheap tier, and the implementation follows
them.)_

### 6.2 Task → tier routing

| Task                                                        | Tier (plain: how powerful a model)       |
| ----------------------------------------------------------- | ---------------------------------------- |
| query classification, source relevance, metadata extraction | small (cheap, fast)                      |
| claim extraction                                            | medium                                   |
| research reasoning, criticism                               | strong                                   |
| final synthesis                                             | strongest (most capable, most expensive) |

### 6.3 Gateway features

```
                 LLM Gateway
                      │
       ┌──────────────┼───────────────┐
       ▼              ▼               ▼
   Provider A     Provider B     Local Ollama
```

- Rate limiting (per provider).
- Retry with backoff + jitter; provider fallback on persistent failure.
- Per-request timeout.
- Token-budget enforcement (per run, via `BudgetLedger`).
- Cost tracking → `llm_calls` table.
- **Concurrency semaphore**: e.g. 8 active calls; the rest queue
  (50 researchers → 8 active, 42 queued).
- Request de-duplication (hash of provider + model + prompt + params).
- Caching of deterministic operations only (see Section 13).
- Every call recorded with `prompt_version`, tokens, cost, latency, trace/span
  ids.

---

## 7. Data architecture

> **In plain terms:** Postgres is the master filing cabinet and the single
> source of truth. The "search by meaning" index (pgvector) lives inside it. Big
> files (PDFs, saved pages, reports) go to S3, not the database. Redis is the
> fast, temporary layer. The tables below are the filing system: one row per
> user, per run, per source, per claim, per citation, and so on.

### 7.1 Stores

| Store          | Holds                                                                       |
| -------------- | --------------------------------------------------------------------------- |
| **PostgreSQL** | system of record, all relational tables below                               |
| **pgvector**   | `document_chunks.embedding` (co-located in Postgres)                        |
| **S3**         | PDFs, raw HTML, screenshots, parsed docs, generated reports, eval artifacts |
| **Redis**      | queue, cache, rate limit, locks, SSE fan-out, temp state                    |

Large documents are **never** stored in Postgres, only a storage key.

Those keys are derived in one place and follow a fixed layout, so that deleting
a user's data, expiring artifacts by age and attributing storage cost to a run
are all prefix operations rather than joins:

```
runs/{run_id}/{kind}/{name}          kind ∈ raw-html | pdf | markdown | text | document | screenshot | report
uploads/{user_id}/{kind}/{name}      a user's files, before a run uses them (ADR 0012)
evaluations/{evaluation_id}/{name}
```

Immutable artifacts are content-addressed on the same SHA-256 that
`documents.content_hash` stores, which makes re-ingesting a source idempotent.
See [ADR 0010](ADRs/0010-object-storage.md) for the interface, the backends and
the failure classification.

### 7.2 Schema (system of record)

> Types are illustrative Postgres. All tables have `id uuid primary key default
gen_random_uuid()`, `created_at timestamptz not null default now()`, and where
> noted `updated_at`. All foreign keys are indexed.
>
> _Plain-terms reading guide: "fk → x" means "points at a row in table x".
> "jsonb" is a flexible sub-record. "vector(N)" is the list of numbers used for
> meaning-search._

#### `users`

| Column        | Type          | Notes                          |
| ------------- | ------------- | ------------------------------ |
| email         | `citext`      | unique, not null               |
| password_hash | `text`        | Auth.js credential flow        |
| name          | `text`        |                                |
| role          | `text`        | `user` \| `admin`              |
| settings      | `jsonb`       | provider/model prefs, UI prefs |
| last_login_at | `timestamptz` |                                |
| updated_at    | `timestamptz` |                                |

#### `sessions`

| Column     | Type          | Notes    |
| ---------- | ------------- | -------- |
| user_id    | `uuid`        | → users  |
| token_hash | `text`        | unique   |
| user_agent | `text`        |          |
| ip         | `inet`        |          |
| expires_at | `timestamptz` |          |
| revoked_at | `timestamptz` | nullable |

#### `research_projects`

| Column      | Type          | Notes    |
| ----------- | ------------- | -------- |
| user_id     | `uuid`        | → users  |
| title       | `text`        |          |
| description | `text`        |          |
| archived_at | `timestamptz` | nullable |
| updated_at  | `timestamptz` |          |

#### `research_runs`

_Plain terms: one row per time you press "Start Research" (or ask a follow-up)._

| Column                  | Type            | Notes                                                                                                                              |
| ----------------------- | --------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| project_id              | `uuid`          | → research_projects                                                                                                                |
| user_id                 | `uuid`          | → users (denormalized for authz)                                                                                                   |
| parent_run_id           | `uuid`          | → research_runs, nullable (conversational followups)                                                                               |
| question                | `text`          | not null                                                                                                                           |
| mode                    | `text`          | `quick` \| `deep` \| `conversational`                                                                                              |
| depth                   | `int`           |                                                                                                                                    |
| domains                 | `text[]`        |                                                                                                                                    |
| date_range_start        | `date`          | nullable                                                                                                                           |
| date_range_end          | `date`          | nullable                                                                                                                           |
| status                  | `text`          | `queued` \| `planning` \| `researching` \| `verifying` \| `synthesizing` \| `validating` \| `completed` \| `failed` \| `cancelled` |
| langgraph_thread_id     | `text`          | ties the run to its saved workflow state                                                                                           |
| langgraph_checkpoint_id | `text`          | last save-point                                                                                                                    |
| iteration_count         | `int`           | default 0                                                                                                                          |
| total_cost_usd          | `numeric(10,4)` | default 0                                                                                                                          |
| total_tokens            | `bigint`        | default 0                                                                                                                          |
| source_count            | `int`           | default 0                                                                                                                          |
| coverage_caveat         | `text`          | set when a hard limit truncated the run                                                                                            |
| started_at              | `timestamptz`   |                                                                                                                                    |
| completed_at            | `timestamptz`   |                                                                                                                                    |
| error                   | `jsonb`         | nullable                                                                                                                           |

Indexes: `(user_id, created_at desc)`, `(project_id)`, `(status)`,
`(parent_run_id)`.

#### `research_tasks` (planner subtasks)

_Plain terms: the smaller questions the Planner created for this run._

| Column       | Type          | Notes                                                  |
| ------------ | ------------- | ------------------------------------------------------ |
| run_id       | `uuid`        | → research_runs                                        |
| external_id  | `text`        | e.g. `market`, `competitors`                           |
| question     | `text`        |                                                        |
| priority     | `text`        | `high` \| `medium` \| `low`                            |
| status       | `text`        | `pending` \| `researching` \| `done` \| `insufficient` |
| rationale    | `text`        | why the planner created it                             |
| iteration    | `int`         | which loop created it                                  |
| completed_at | `timestamptz` |                                                        |

#### `sources`

_Plain terms: every document reference discovered: one row per web page,
filing, paper, repo, or upload._

| Column               | Type           | Notes                                                         |
| -------------------- | -------------- | ------------------------------------------------------------- |
| run_id               | `uuid`         | → research_runs (nullable; sources may be shared across runs) |
| url                  | `text`         | original                                                      |
| canonical_url        | `text`         | after canonicalization (dedupe)                               |
| domain               | `text`         |                                                               |
| source_type          | `text`         | `web` \| `sec` \| `arxiv` \| `github` \| `upload`             |
| title                | `text`         |                                                               |
| publisher            | `text`         |                                                               |
| author               | `text`         |                                                               |
| published_at         | `timestamptz`  | nullable                                                      |
| accessed_at          | `timestamptz`  |                                                               |
| content_hash         | `text`         | sha256 of normalized content (exact-dupe key)                 |
| credibility_score    | `numeric(3,2)` |                                                               |
| credibility_metadata | `jsonb`        | domain reputation, is_primary, tier                           |
| dedup_cluster_id     | `uuid`         | groups near-duplicates                                        |

Indexes: `(run_id)`, `(canonical_url)`, `(content_hash)`, `(dedup_cluster_id)`,
`(source_type)`.

#### `documents`

_Plain terms: the actual cleaned-up text of a source, plus a pointer to the raw
file in S3._

| Column             | Type     | Notes                                         |
| ------------------ | -------- | --------------------------------------------- |
| source_id          | `uuid`   | → sources                                     |
| storage_key        | `text`   | S3 key for raw + parsed artifacts             |
| mime_type          | `text`   |                                               |
| raw_size_bytes     | `bigint` |                                               |
| normalized_content | `text`   | boilerplate-stripped text                     |
| language           | `text`   | ISO code from language detection              |
| extraction_method  | `text`   | reader/parser used                            |
| token_count        | `int`    |                                               |
| content_hash       | `text`   | sha256 (idempotent ingestion key)             |
| metadata           | `jsonb`  | page count, language method, chunker settings |

Unique: `(source_id, content_hash)`. Per source, not global: two runs holding the
same document keep their own rows, so one user deleting a run can never cascade
into another user's evidence (ADR 0012).

#### `document_chunks`

_Plain terms: each document sliced into searchable pieces; each piece has a
meaning-vector and a keyword index._

| Column          | Type                | Notes                                                 |
| --------------- | ------------------- | ----------------------------------------------------- |
| document_id     | `uuid`              | → documents                                           |
| chunk_index     | `int`               |                                                       |
| content         | `text`              |                                                       |
| token_count     | `int`               |                                                       |
| embedding       | `vector(EMBED_DIM)` | pgvector; HNSW index (meaning-search)                 |
| embedding_model | `text`              |                                                       |
| tsv             | `tsvector`          | generated from `content` for keyword/full-text search |
| metadata        | `jsonb`             | section, page, offsets, source_type (for filters)     |

Indexes: `USING hnsw (embedding vector_cosine_ops)`, `USING gin (tsv)`,
`(document_id, chunk_index)`, `gin (metadata)`.

`EMBED_DIM` is 768, the width of the one declared embedding model
(`nomic-embed-text`). The ingestion pipeline refuses to start against a model of
another width; changing model families means a migration and a re-embed
(ADR 0012). `embedding_model` is written in the same statement as `embedding`,
so a null `embedding_model` marks a chunk that is still waiting for its vector.

#### `uploads`

_Plain terms: a file a user uploaded, kept until a research run uses it._

| Column       | Type     | Notes                                           |
| ------------ | -------- | ----------------------------------------------- |
| user_id      | `uuid`   | → users                                         |
| filename     | `text`   | cleaned for display; never used to build a path |
| format       | `text`   | `pdf` \| `html` \| `markdown` \| `text`         |
| mime_type    | `text`   | canonical for the format, not the client's      |
| size_bytes   | `bigint` |                                                 |
| content_hash | `text`   | sha256 of the bytes as uploaded                 |
| storage_key  | `text`   | under `uploads/{user_id}/`                      |
| charset      | `text`   | declared charset of a text format, nullable     |

Unique: `(user_id, content_hash)`, per user and never global. Indexes:
`(user_id, created_at desc, id)`.

#### `research_run_uploads`

_Plain terms: which uploaded files a run was created with._

| Column    | Type   | Notes               |
| --------- | ------ | ------------------- |
| run_id    | `uuid` | → research_runs, PK |
| upload_id | `uuid` | → uploads, PK       |

#### `claims`

_Plain terms: one row per distinct statement the research produced._

| Column         | Type           | Notes                                                                |
| -------------- | -------------- | -------------------------------------------------------------------- |
| run_id         | `uuid`         | → research_runs                                                      |
| task_id        | `uuid`         | → research_tasks, nullable                                           |
| text           | `text`         | normalized atomic assertion                                          |
| subject        | `text`         |                                                                      |
| predicate      | `text`         |                                                                      |
| object_value   | `text`         |                                                                      |
| claim_type     | `text`         | `quantitative` \| `qualitative` \| `event`                           |
| normalized_key | `text`         | groups the same claim across sources; drives contradiction detection |
| confidence     | `numeric(3,2)` | calibrated, from the Verification Agent                              |
| status         | `text`         | `candidate` \| `verified` \| `refuted` \| `contested`                |
| first_seen_at  | `timestamptz`  |                                                                      |

Indexes: `(run_id)`, `(normalized_key)`, `(status)`.

#### `evidence`

_Plain terms: the exact quote that supports (or refutes) a claim, and where it
came from._

| Column          | Type           | Notes                                |
| --------------- | -------------- | ------------------------------------ |
| claim_id        | `uuid`         | → claims                             |
| document_id     | `uuid`         | → documents                          |
| source_id       | `uuid`         | → sources                            |
| span_text       | `text`         | verbatim                             |
| span_start      | `int`          | char offset in `normalized_content`  |
| span_end        | `int`          |                                      |
| stance          | `text`         | `supports` \| `refutes` \| `neutral` |
| extractor_agent | `text`         |                                      |
| extractor_model | `text`         |                                      |
| confidence      | `numeric(3,2)` |                                      |

Indexes: `(claim_id)`, `(source_id)`, `(document_id)`.

#### `contradictions`

_Plain terms: one row per genuine disagreement between sources, with a best-guess
reason._

| Column         | Type   | Notes                                                                   |
| -------------- | ------ | ----------------------------------------------------------------------- |
| run_id         | `uuid` | → research_runs                                                         |
| normalized_key | `text` | the disputed claim key                                                  |
| claim_a_id     | `uuid` | → claims                                                                |
| claim_b_id     | `uuid` | → claims                                                                |
| value_a        | `text` |                                                                         |
| value_b        | `text` |                                                                         |
| likely_reason  | `text` | e.g. "different fiscal periods"                                         |
| resolution     | `text` | `unresolved` \| `resolved_a` \| `resolved_b` \| `both_valid_in_context` |
| resolved_by    | `text` | agent or user                                                           |

#### `reports`

| Column             | Type           | Notes                                 |
| ------------------ | -------------- | ------------------------------------- |
| run_id             | `uuid`         | → research_runs, unique               |
| title              | `text`         |                                       |
| summary            | `text`         | executive summary cache               |
| overall_confidence | `numeric(3,2)` |                                       |
| status             | `text`         | `draft` \| `validated` \| `published` |
| model              | `text`         | synthesizer model                     |
| word_count         | `int`          |                                       |
| generated_at       | `timestamptz`  |                                       |
| validated_at       | `timestamptz`  | citation-validation pass time         |

#### `report_sections`

| Column     | Type   | Notes                                                                                                                                                                                     |
| ---------- | ------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| report_id  | `uuid` | → reports                                                                                                                                                                                 |
| kind       | `text` | `executive_summary` \| `key_findings` \| `detailed_analysis` \| `competitive_landscape` \| `evidence` \| `contradictions` \| `confidence_assessment` \| `recommendations` \| `references` |
| heading    | `text` |                                                                                                                                                                                           |
| ordinal    | `int`  |                                                                                                                                                                                           |
| content_md | `text` | Markdown with `[n]` markers                                                                                                                                                               |

#### `citations`

_Plain terms: the link behind every `[n]` in the report._

| Column            | Type   | Notes                                             |
| ----------------- | ------ | ------------------------------------------------- |
| report_section_id | `uuid` | → report_sections                                 |
| claim_id          | `uuid` | → claims                                          |
| source_id         | `uuid` | → sources                                         |
| ordinal           | `int`  | the `[n]` number within the report                |
| quote             | `text` | the supporting evidence span shown on hover/click |

Indexes: `(report_section_id)`, `(claim_id)`, `(source_id)`.

#### `agent_runs`

_Plain terms: one row per AI-worker execution; the step-by-step activity log._

| Column                    | Type            | Notes                                                                                                                                      |
| ------------------------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| run_id                    | `uuid`          | → research_runs                                                                                                                            |
| task_id                   | `uuid`          | → research_tasks, nullable                                                                                                                 |
| agent_name                | `text`          | `planner` \| `researcher` \| `evidence_extractor` \| `claim_normalizer` \| `verifier` \| `critic` \| `synthesizer` \| `citation_validator` |
| iteration                 | `int`           |                                                                                                                                            |
| status                    | `text`          | `running` \| `ok` \| `error`                                                                                                               |
| input                     | `jsonb`         |                                                                                                                                            |
| output                    | `jsonb`         |                                                                                                                                            |
| latency_ms                | `int`           |                                                                                                                                            |
| tokens                    | `int`           |                                                                                                                                            |
| cost_usd                  | `numeric(10,4)` |                                                                                                                                            |
| trace_id                  | `text`          | OpenTelemetry                                                                                                                              |
| span_id                   | `text`          |                                                                                                                                            |
| error                     | `jsonb`         | nullable                                                                                                                                   |
| started_at / completed_at | `timestamptz`   |                                                                                                                                            |

#### `tool_calls`

_Plain terms: one row per external action: a search, a fetch, or an API call._

| Column             | Type            | Notes                                                                                    |
| ------------------ | --------------- | ---------------------------------------------------------------------------------------- |
| agent_run_id       | `uuid`          | → agent_runs                                                                             |
| tool_name          | `text`          | `search` \| `fetch` \| `parse` \| `retrieve` \| `sec_api` \| `arxiv_api` \| `github_api` |
| request            | `jsonb`         |                                                                                          |
| response_summary   | `jsonb`         | truncated; full body in S3 if large                                                      |
| status             | `text`          | `ok` \| `error` \| `rate_limited` \| `timeout`                                           |
| latency_ms         | `int`           |                                                                                          |
| cost_usd           | `numeric(10,4)` |                                                                                          |
| cache_hit          | `bool`          |                                                                                          |
| retries            | `int`           |                                                                                          |
| trace_id / span_id | `text`          |                                                                                          |
| error              | `jsonb`         | nullable                                                                                 |

#### `llm_calls`

_Plain terms: one row per AI model call, with its token count and cost._

| Column                            | Type            | Notes                                           |
| --------------------------------- | --------------- | ----------------------------------------------- |
| agent_run_id                      | `uuid`          | → agent_runs, nullable                          |
| role                              | `text`          | planner/researcher/...                          |
| provider                          | `text`          | `openai` \| `anthropic` \| `gemini` \| `ollama` |
| model                             | `text`          |                                                 |
| prompt_version                    | `text`          | from `packages/prompts`                         |
| prompt_tokens / completion_tokens | `int`           |                                                 |
| cost_usd                          | `numeric(10,4)` |                                                 |
| latency_ms                        | `int`           |                                                 |
| temperature                       | `numeric(3,2)`  |                                                 |
| cache_hit                         | `bool`          |                                                 |
| status                            | `text`          | `ok` \| `error` \| `fallback`                   |
| request_hash                      | `text`          | for de-dupe                                     |
| trace_id / span_id                | `text`          |                                                 |

#### `evaluations`

_Plain terms: one row per test-question result in a benchmark run._

| Column          | Type    | Notes                                                                   |
| --------------- | ------- | ----------------------------------------------------------------------- |
| run_id          | `uuid`  | → research_runs, nullable (benchmark rows have none)                    |
| benchmark_id    | `text`  | dataset case id                                                         |
| dataset_version | `text`  |                                                                         |
| git_sha         | `text`  | commit under test                                                       |
| kind            | `text`  | `retrieval` \| `generation` \| `agent` \| `infrastructure` \| `full`    |
| metrics         | `jsonb` | recall@k, precision@k, mrr, ndcg, faithfulness, citation_precision, ... |
| thresholds      | `jsonb` | gate values                                                             |
| passed          | `bool`  |                                                                         |

#### `feedback`

| Column    | Type   | Notes                                                                   |
| --------- | ------ | ----------------------------------------------------------------------- |
| run_id    | `uuid` | → research_runs                                                         |
| report_id | `uuid` | → reports                                                               |
| user_id   | `uuid` | → users                                                                 |
| rating    | `int`  | 1-5                                                                     |
| helpful   | `bool` |                                                                         |
| category  | `text` | `accuracy` \| `completeness` \| `citations` \| `readability` \| `other` |
| comment   | `text` |                                                                         |

### 7.3 Connection management

_Plain terms: databases have a limited number of "phone lines"; a pooler shares
them efficiently so the app never runs out._

- PgBouncer (transaction pooling) in front of Postgres.
- Separate pools for API (short transactions) and Worker (longer transactions).
- Statement timeout on the API pool; longer on the worker pool.

---

## 8. RAG pipeline (`apps/api/app/retrieval`)

> **In plain terms:** RAG = "read the sources, then answer from them". This is
> the librarian's process: take a document, clean it, cut it into pieces, record
> each piece's meaning and keywords, and file it. Later, given a question, find
> the best pieces by keyword **and** by meaning, then re-sort them by true
> relevance before handing them to the writer.

Built on LlamaIndex.

### 8.1 Ingestion

```
document (a fetched page, or an upload attached to the run)
  → copy the raw bytes under runs/{run_id}/ (content-addressed)
  → parse in a killable child process (pypdf / readability / strict decoding)
  → normalize (sanitized; this is documents.normalized_content, never rewritten)
  → language detection (py3langid; under 0.80 confidence recorded as unknown)
  → chunk (LlamaIndex SentenceSplitter + MarkdownNodeParser; 512 tokens, 64 overlap)
  → write source + document + document_chunks (tsv generated; no vectors yet)
  → embed in batches through the gateway → write vectors
```

- **Isolated parsing.** Each document is parsed by a fresh Python process with a
  scrubbed environment and network access refused, killed at
  `PARSE_TIMEOUT_SECONDS`, and on POSIX run under an address-space and CPU
  ceiling (threat model 3.6, ADR 0012).
- **Exact offsets.** Every chunk records `char_start` and `char_end` into the
  normalized text, and `text[start:end]` is the chunk. Positions are computed
  and checked rather than trusted from the splitter, and only whitespace may
  fall between chunks, so nothing is lost. Evidence spans resolve through these
  offsets.
- **Two writes.** Chunks are committed before they are embedded, a batch per
  transaction. A failed embedding call costs one batch; the retry embeds only
  the chunks whose `embedding_model` is still null.
- **Idempotent.** A source is found by run and canonical URL before one is
  created, under a transaction advisory lock, and a document is unique per
  (source, content hash). Re-ingesting the same content is a no-op, including
  when a job is delivered twice at once.
- **Chunk size.** 512/64, now measured rather than assumed: Phase 8's benchmark
  sweeps the size over a labelled corpus, and 512/64 ranks best of the three
  sizes tried on the metrics that read order. Each chunk still records the
  settings that produced it. The numbers, and what they are and are not worth,
  are in `data/eval/retrieval/README.md`.

### 8.2 Retrieval

```
query
  → embed it with the query prefix (asymmetric models want a different one
    from the passages they are compared against)
  → dense arm: pgvector cosine over chunks embedded by the same model
  ‖ lexical arm: the generated `tsv` column, query terms OR-ed, ts_rank_cd
  → fuse by reciprocal rank (k = 60, per-arm weights)
  → rerank for diversity (MMR) → top ~8-12
```

- **Hybrid:** vector search (pgvector cosine, "by meaning") + Postgres
  full-text (`tsv`, "by keyword"), fused with Reciprocal Rank Fusion. The arms
  run concurrently, so a hybrid call costs the slower of the two.
- **Lexical, not BM25.** `ts_rank_cd` over the generated column, with the
  query's terms **OR-ed**: `websearch_to_tsquery` combines them with AND, which
  means a natural-language question matches nothing. Fusion consumes ranks
  rather than scores, so what this arm has to get right is the ordering.
  Reasoning and the measurement are in ADR 0013.
- **Metadata filters:** every filter in `ChunkFilter`, and both arms narrow
  through one builder so a filter cannot be honoured by one and ignored by the
  other. Run scope and `user_id` ownership are in the same WHERE clause.
- **Reranking:** `Reranker` is the seam. What ships is **MMR**, a *diversity*
  reranker, because the top of a fused list is full of near-duplicates. A
  cross-encoder re-scores true relevance and is the right eventual answer for a
  different problem; it drops in behind the same interface, and the benchmark
  decides whether it earns its latency (ADR 0013).
- **Honest emptiness:** an arm that could not run says so, with a reason. No
  embedding model configured, nothing embedded yet, an arm weighted to zero -
  each is a *skipped* arm on the result, never a quietly shorter list.
- **Benchmarked:** `scripts/benchmark_retrieval.py` scores the strategies and
  sweeps the chunk size against recall. Labels, method and the measured
  baseline are in `data/eval/retrieval/`.
- **Per-document query engines:** for targeted questions against a single filing
  or paper. Available through `ChunkFilter(document_ids=...)`; a dedicated
  engine object is not built.
- **Knowledge graph:** entities (companies, products, people, funding rounds) and
  relations extracted during claim normalization, stored as claim
  subject/predicate/object plus a lightweight `kg_edges` projection; used to
  expand retrieval ("competitors of X") and to group contradictions.

### 8.3 Caching in RAG

- Embedding cache: `sha256(text) + embedding_model` → vector.
- Retrieval results are **not** cached across runs (freshness matters), but
  within a run identical retrieval calls are memoized.

---

## 9. Web content pipeline & deduplication (`apps/api/app/sources`)

> **In plain terms:** search gives URLs; the system fetches each page safely,
> pulls out the real article text, throws away menus and ads, detects the
> language, removes duplicates, and files it. Duplicate removal matters: ten
> reposts of one wire story should count as **one** source, not ten, or
> confidence scores get inflated.

### 9.1 Pipeline

```
Search  →  URL  →  Fetch  →  Content extraction  →  Boilerplate removal
       →  Language detection  →  Deduplication  →  Chunking  →  Embedding  →  Storage
```

Store original source + normalized content + hash.

### 9.2 Fetcher

- SSRF guard (see Section 15) before every request and after every redirect.
- Timeout + retry with backoff + jitter.
- Respects `robots.txt`; sends a descriptive User-Agent.
- Raw HTML archived to S3; main content extracted with a readability extractor.

### 9.3 Deduplication

Three signals, combined:

1. **URL canonicalization**: strip tracking parameters, normalize host/scheme,
   resolve AMP/mobile variants.
2. **Content hash**: exact-duplicate detection on normalized content.
3. **Semantic similarity**: embedding cosine ≥ threshold clusters near-dupes
   (syndicated wire stories, aggregator copies).

Clustered sources share a `dedup_cluster_id`. **Confidence weighting counts one
cluster as one independent source**, not N copies.

### 9.4 SearchBudget

_Plain terms: a per-run spending cap for searching and fetching._

| Field         | Example                   |
| ------------- | ------------------------- |
| `max_queries` | 30                        |
| `max_results` | per-query cap             |
| `max_domains` | diversity cap             |
| `max_pages`   | 100                       |
| `timeout`     | 5 min                     |
| `cost_limit`  | contributes to `max_cost` |

---

## 10. Execution model & streaming

> **In plain terms:** you never wait at the counter. Your request returns
> instantly with a ticket number. A background worker does the multi-minute job
> and pushes live updates ("planning...", "reading 14 sources...", "verifying...") to
> your browser through a one-way live feed.

### 10.1 Async job lifecycle

```
POST /research
   → validate (schema)
   → create research_runs row (status = queued)
   → enqueue {run_id} on Redis
   → 202 Accepted { run_id }

worker:
   → claim job
   → load/create LangGraph thread (Postgres checkpointer)
   → invoke graph, streaming node events
   → after each node: persist domain rows + emit SSE event + write checkpoint
   → on terminal node: persist report, status = completed
```

### 10.2 SSE (Server-Sent Events)

- Endpoint: `GET /research/{id}/events` (`text/event-stream`).
- The worker publishes events to a Redis channel `run:{id}:events`; the API
  subscribes and relays to the client.
- Event types: `status`, `plan`, `subtask_started`, `source_found`,
  `sources_progress`, `evidence_progress`, `contradiction_found`, `iteration`,
  `synthesizing`, `citation_check`, `completed`, `error`.
- Each event carries an increasing `seq`; clients reconnect with `Last-Event-ID`
  and the API replays from a bounded Redis buffer.
- A heartbeat comment every 15 s keeps intermediaries from closing the stream.

---

## 11. Durability & resumability

> **In plain terms:** the workflow writes a save-point after every step. If the
> worker crashes or is redeployed mid-run, a supervisor notices and the workflow
> continues from the last save-point. It does not restart and does not re-spend
> money, because repeated steps are designed to be harmless.

- LangGraph **Postgres checkpointer**: full graph state is persisted after each
  node (`thread_id = research_runs.langgraph_thread_id`).
- A run can resume after: worker crash, deploy, transient provider outage, or a
  user-initiated pause.
- The worker supervisor scans for runs `status in (planning, researching,
verifying, synthesizing)` with a stale heartbeat and re-enqueues them; the
  graph resumes from the last checkpoint (no repeated side effects because
  ingestion and writes are idempotent on hashes / natural keys).
- `POST /research/{id}/cancel` sets a Redis flag; the graph checks it between
  nodes, writes a final checkpoint, and sets `status = cancelled`.
- Human-in-the-loop: the graph supports an interrupt before `Synthesizer` in a
  "review the plan first" configuration (off by default; used for high-stakes
  runs).

---

## 12. Failure handling

> **In plain terms:** anything that talks to the outside world will sometimes
> fail. Every such call has a time limit and is retried a few times with growing
> pauses. Failures are sorted into types so each is handled correctly, a
> "slow down" is not treated the same as a "you're not allowed".

### 12.1 Every external operation gets

`timeout`, `retry`, `exponential backoff`, `jitter`.

```
attempt 1 → fail → wait ~1s (+random jitter)
attempt 2 → fail → wait ~2s (+random jitter)
attempt 3 → fail → dead-letter / fallback
```

### 12.2 Error taxonomy

| Class               | Plain meaning                      | Handling                                                      |
| ------------------- | ---------------------------------- | ------------------------------------------------------------- |
| **Transient**       | temporary blip                     | retry with backoff                                            |
| **Rate limit**      | "you're going too fast"            | retry honoring `Retry-After`; gateway throttles that provider |
| **Auth**            | "your key is wrong / not allowed"  | no retry; alert; fail that call, continue the run if possible |
| **Timeout**         | no response in time                | retry once with a longer budget, then dead-letter             |
| **Content failure** | page unparseable / empty / blocked | drop the source, log it, continue                             |
| **Permanent**       | malformed request (a bug)          | no retry; surface the error                                   |

### 12.3 Circuit breaking

_Plain terms: if a provider keeps failing, stop calling it for a while instead of
hammering it._

- Per provider and per external API: open the breaker after a failure-rate
  threshold; route to a fallback provider or skip the connector; half-open probe
  after a cooldown.

### 12.4 Dead-letter queue

- Jobs that exhaust retries land on a dead-letter queue with full context for
  manual inspection / replay.

---

## 13. Caching strategy

> **In plain terms:** don't pay twice for the same work. If the same search,
> page fetch, or deterministic AI step was done recently, reuse the stored
> result. The one thing never cached blindly is a final research answer, those
> must stay fresh.

| Cache           | Key                                          | TTL                  | Notes                                                                                                                            |
| --------------- | -------------------------------------------- | -------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| **Search**      | `sha256(query + domains + date_range)`       | short (hours)        | avoids duplicate search spend within/near a run                                                                                  |
| **URL / fetch** | `url + content_hash`                         | medium (days)        | serves the archived normalized content                                                                                           |
| **Embedding**   | `sha256(text) + embedding_model`             | long                 | embeddings are deterministic                                                                                                     |
| **LLM**         | `sha256(provider + model + prompt + params)` | short, **selective** | only deterministic sub-ops (classification, metadata extraction, normalization). **Never blindly cache final research answers.** |

Cache lookups are recorded (`cache_hit` on `tool_calls` / `llm_calls`) so the
evaluation system can report cost savings.

### 13.1 Prompt / context optimization

_Plain terms: ways to cut the AI bill without hurting quality._

- Keep system prompts stable (better provider-side prompt caching; stable
  `prompt_version`).
- Summarize old research notes before re-feeding them into a loop iteration.
- Retrieve only relevant evidence into the synthesis context.
- Deduplicate source text in-context.
- Use small models for classification; reserve the strongest models for
  synthesis and critical reasoning.

---

## 14. Concurrency & backpressure

> **In plain terms:** if 500 people start research at once, the system does not
> launch thousands of AI agents. It runs a fixed number at a time and everything
> else waits politely in line.

```
API  →  Queue  →  Concurrency manager  →  Worker pool
```

Global caps (Redis-coordinated):

| Cap                          | Value                                   |
| ---------------------------- | --------------------------------------- |
| max concurrent research runs | 20                                      |
| max concurrent searches      | 50                                      |
| max concurrent LLM requests  | 30 (gateway semaphore; ~8 per provider) |

- 500 submitted runs do **not** launch 5,000 agents; excess runs wait in the
  queue.
- A per-user concurrency cap stops one user starving everyone else.
- The worker pulls work only when it has capacity (pull, not push).

---

## 15. Security architecture

> **In plain terms:** assume the internet is hostile. Never trust fetched text as
> an instruction. Never let the server be tricked into calling internal
> addresses. Give the AI agents the smallest possible set of tools. Keep secrets
> on the server. Treat uploaded files as potentially dangerous. Scrub the final
> report.

Full detail in `docs/threat-model.md`. Summary:

### 15.1 Prompt injection

- All retrieved content is **untrusted data**. Researcher/extractor prompts wrap
  fetched text in explicit delimiters and instruct the model that the content
  inside is data to analyse, never instructions to follow.
- Output filtering on synthesized text: strip anything resembling injected
  directives, system-prompt fragments, or secret-shaped tokens.
- Agents have a **restricted tool set**: see 15.3.

### 15.2 SSRF (Server-Side Request Forgery)

- Deny fetches to `localhost`, `127.0.0.0/8`, `10.0.0.0/8`, `172.16.0.0/12`,
  `192.168.0.0/16`, `169.254.0.0/16` (incl. the cloud metadata address
  `169.254.169.254`), `::1`, and link-local / unique-local IPv6.
- Resolve the hostname and validate **every** resolved IP before connecting;
  re-validate after each redirect; refuse non-http(s) schemes.
- Domain allowlist/denylist configurable per deployment and per research request.

### 15.3 Tool permissions

Tiered: **read** / **write** / **admin**. Research agents get **only**:
`search`, `fetch`, `parse`, `retrieve`. No shell, no filesystem writes, no admin
tools, no arbitrary code execution.

### 15.4 Secrets

- All provider keys and DB credentials are server-side only; injected via
  environment / secret manager; never shipped to the browser; never placed in
  LLM context or URLs.

### 15.5 Malicious documents

- Size and file-type limits before an upload URL is issued.
- Parsing in a resource-limited sandbox; no macro execution; PDF JavaScript
  disabled; zip/archive bombs rejected by ratio + size caps.

### 15.6 AuthN / AuthZ

- Auth.js sessions; every research resource checked against `user_id`.
- Rate limiting per user + route + cost weight.

---

## 16. Observability (`apps/api/app/observability`)

> **In plain terms:** for every research run we keep a detailed "flight
> recorder": which step ran, how long it took, how many tokens and dollars it
> used, which model, which prompt version, and what it produced or failed on.
> Dashboards and alerts sit on top of that.

### 16.1 Tracing

Per research run, a hierarchical trace:

```
Research Run
 ├── Planner
 │    └── LLM call
 ├── Researcher A
 │    ├── Search
 │    ├── Fetch
 │    └── Extraction LLM
 ├── Researcher B
 │    ├── Search
 │    └── ...
 ├── Verification
 ├── Critic
 └── Synthesizer
      └── LLM call
```

Each node/span records: `latency`, `tokens`, `cost`, `model`, `prompt_version`,
`result`, `error`.

### 16.2 Stack

| Concern            | Tool                                                      |
| ------------------ | --------------------------------------------------------- |
| Traces / spans     | OpenTelemetry → LangSmith (LLM-aware) + an OTLP collector |
| Metrics            | Prometheus                                                |
| Dashboards         | Grafana                                                   |
| LLM run inspection | LangSmith                                                 |
| Logs               | structured JSON, correlation id = `run_id` + `trace_id`   |

### 16.3 Key metrics

- `research_run_duration_seconds{mode}` (histogram) → P50/P95/P99.
- `research_run_cost_usd{mode}` (histogram).
- `tool_call_failures_total{tool,class}`.
- `llm_call_latency_seconds{provider,model}`.
- `llm_gateway_queue_depth`, `llm_gateway_active`.
- `research_queue_depth`, `active_research_runs`.
- `citation_validation_failures_total`.

### 16.4 Alerts

- P95 deep latency > 180 s for 10 minutes.
- Cost/run above threshold.
- Tool failure rate > 5%.
- Queue depth rising for 15 minutes (worker starvation).
- Citation-validation failure rate > 2%.

---

## 17. Evaluation system (`packages/evaluation`)

> **In plain terms:** a permanent, versioned exam. 100-300 questions with known
> good answers and sources. The system sits the exam after every change; the
> results are stored and charted; a failing grade blocks the release.

### 17.1 Dataset

- 100-300 cases in `data/eval/`, versioned. Categories: technology, business,
  finance, science, current events, competitive analysis, historical, multi-hop,
  contradictory-evidence.
- Case schema: `{ question, expected_topics[], expected_sources[], must_cite }`.

### 17.2 Runner

- `make eval` runs the benchmark against the current build; writes rows to
  `evaluations` and artifacts to S3.
- Modes: `retrieval`, `generation`, `agent`, `infrastructure`, `full`.

### 17.3 Metrics

| Layer          | Metrics                                                                                    |
| -------------- | ------------------------------------------------------------------------------------------ |
| Retrieval      | Recall@K, Precision@K, MRR, NDCG                                                           |
| Generation     | answer correctness, faithfulness, groundedness, citation precision, citation recall        |
| Agent          | task success, tool selection accuracy, planning accuracy, unnecessary calls, recovery rate |
| Infrastructure | P50, P95, P99, throughput, cost, failure rate                                              |

Grading: deterministic checks where possible (does the citation resolve? is the
source in `expected_sources`? is the topic covered?); an LLM-as-judge with a
rubric for correctness / faithfulness, sampled and spot-audited by a human.

### 17.4 CI gate

- PR: unit + integration + agent + RAG tests.
- Main: the above + the full evaluation benchmark + Docker build.
- The build **fails** when a gated metric drops below threshold (e.g. citation
  correctness < 90%).
- A trend view in `/evaluations` compares against the last N commits.

---

## 18. API surface (selected)

> **In plain terms:** the list of requests the front counter accepts.

| Method | Path                                            | Purpose                          |
| ------ | ----------------------------------------------- | -------------------------------- |
| `POST` | `/auth/register`                                | create account                   |
| `POST` | `/auth/login`                                   | start session                    |
| `POST` | `/auth/logout`                                  | end session                      |
| `GET`  | `/auth/sessions` / `DELETE /auth/sessions/{id}` | list / revoke sessions           |
| `POST` | `/research`                                     | create run → `202 { run_id }`    |
| `GET`  | `/research`                                     | list the current user's runs     |
| `GET`  | `/research/{id}`                                | run status + summary             |
| `GET`  | `/research/{id}/events`                         | SSE progress stream              |
| `GET`  | `/research/{id}/sources`                        | sources + duplicate clusters     |
| `GET`  | `/research/{id}/evidence`                       | claims, evidence, contradictions |
| `GET`  | `/research/{id}/activity`                       | the agent / tool / LLM trace     |
| `GET`  | `/research/{id}/report`                         | report + sections + citations    |
| `POST` | `/research/{id}/followup`                       | conversational child run         |
| `POST` | `/research/{id}/cancel`                         | cooperative cancel               |
| `POST` | `/files`                                        | upload a document (Section 3.5)  |
| `GET`  | `/files` / `/files/{id}`                        | the caller's uploads             |
| `POST` | `/feedback`                                     | rate a report                    |
| `GET`  | `/evaluations`                                  | dashboard data                   |
| `POST` | `/evaluations/run`                              | (admin) trigger a benchmark      |
| `GET`  | `/health` / `/health/ready`                     | liveness / readiness             |

All list endpoints are cursor-paginated and scoped to the authenticated user.

---

## 19. Testing strategy

> **In plain terms:** many small tests for individual functions, medium tests
> that check the parts connect, behavioural tests that check the agents actually
> do the right thing, the full self-grading exam, and an end-to-end test that
> drives a real browser through the whole product.

| Level                | Targets                                                                                                                                                             |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Unit**             | query parser, source parser, URL validator, citation parser, ranking functions, cost calculator, token budget, state transitions                                    |
| **Integration**      | API↔DB, API↔Redis, Worker↔LangGraph, Retriever↔pgvector, Search↔evidence DB                                                                                         |
| **Agent**            | behavioural: "Compare company A and B" then planner creates a competitor task, researcher uses web search, critic verifies evidence, synthesizer produces citations |
| **RAG**              | retrieval recall/precision on a fixed fixture corpus; reranker improves ordering; metadata filters honoured                                                         |
| **Evaluation**       | `benchmark.json` run against each release                                                                                                                           |
| **E2E (Playwright)** | login → new research → submit → live activity → wait for completion → open report → click citation → view source                                                    |

Fixtures in `data/fixtures/`. External APIs are recorded/replayed (VCR-style) in
CI; a nightly job runs a small live subset.

---

## 20. CI/CD

> **In plain terms:** every proposed change is automatically linted, type-checked,
> tested, security-scanned, and built before anyone merges it. Merges to the main
> branch additionally sit the full exam. Production deploys build an image, ship
> it, and confirm it is healthy.

| Trigger        | Pipeline                                                          |
| -------------- | ----------------------------------------------------------------- |
| **PR**         | lint → typecheck → unit → integration → security scan → build     |
| **Main**       | all PR steps + evaluation benchmark + Docker build                |
| **Production** | build image → push registry → deploy → smoke tests → health check |

- Security scan: dependency audit, secret scan, SAST, container scan.
- Quality gate: evaluation thresholds (see Section 17.4).
- Database migrations run as a pre-deploy step with a rollback path.

---

## 21. Deployment & infrastructure (`infra/`)

> **In plain terms:** everything runs in containers (standard boxes). Locally,
> one command starts the whole stack. In the cloud (AWS), the same containers run
> as managed services, and the entire setup is defined in text files (Terraform)
> so it is repeatable and reviewable.

- **Containers:** `apps/web` (Next.js), `apps/api` (FastAPI), worker (same image,
  different entrypoint).
- **Local:** `docker-compose.yml`, web, api, worker, Postgres+pgvector, Redis,
  MinIO (S3-compatible), OTEL collector, Prometheus, Grafana.
- **Cloud:** AWS. ECS/Fargate or EKS for api + worker; RDS Postgres (with
  pgvector); ElastiCache Redis; S3; ALB; Secrets Manager; CloudWatch + managed
  Grafana.
- **IaC:** Terraform in `infra/terraform`; Kubernetes manifests in
  `infra/kubernetes` for the EKS path; `infra/monitoring` for dashboards + alert
  rules.
- **Scaling:** api scales on request rate; worker scales on
  `active_research_runs` + `research_queue_depth`.
- **Disaster recovery:** automated Postgres snapshots + point-in-time recovery;
  S3 versioning; a documented restore runbook; infra reproducible from Terraform.

---

## 22. Repository structure

```
aether-research/
├── apps/
│   ├── web/                     # Next.js (the website)
│   │   ├── app/ components/ lib/ tests/
│   └── api/                     # FastAPI + worker (front counter + back room)
│       ├── app/
│       │   ├── api/             # request routers
│       │   ├── agents/          # planner, researchers, verifier, critic, synthesizer, citation validator
│       │   ├── research/        # run lifecycle, followups, cancel
│       │   ├── retrieval/       # LlamaIndex ingestion + hybrid retrieval + rerank + KG
│       │   ├── sources/         # connectors (web/SEC/arXiv/GitHub), fetcher, dedup, SearchBudget
│       │   ├── evidence/        # claim normalization, verification, contradictions
│       │   ├── models/          # LLM Gateway, model_registry, routing
│       │   ├── db/              # DB models, migrations, pooling
│       │   ├── workers/         # queue consumers, supervisor, dead-letter queue
│       │   └── observability/   # tracing, metrics, logging
│       └── tests/
├── packages/
│   ├── shared-types/            # shared data definitions (website <-> server)
│   ├── prompts/                 # versioned prompt templates (prompt_version)
│   └── evaluation/              # dataset loaders, runner, metrics, graders
├── data/
│   ├── seed/  eval/  fixtures/
├── infra/
│   ├── docker/  terraform/  kubernetes/  monitoring/
├── docs/
│   ├── PRD.md  TDD.md  architecture.md  threat-model.md  evaluation.md
│   └── ADRs/
├── scripts/
├── .github/workflows/
├── docker-compose.yml
├── Makefile
├── README.md
└── LICENSE
```

---

## 23. Architecture Decision Records

> **In plain terms:** short notes recording _why_ each big choice was made, so a
> future reader doesn't have to guess.

| ADR                                  | Decision                                                                                       |
| ------------------------------------ | ---------------------------------------------------------------------------------------------- |
| `0001-framework-split.md`            | LlamaIndex = retrieval, LangGraph = orchestration, LangChain = model/tool layer; no overlap    |
| `0002-async-execution.md`            | HTTP returns `202`; research runs on a worker via a Redis queue                                |
| `0003-postgres-pgvector.md`          | One Postgres for relational + vectors in v1; revisit a dedicated vector DB at scale            |
| `0004-auth-authjs-postgres.md`       | Auth.js + Postgres instead of a hosted auth product, to own the architecture                   |
| `0005-llm-gateway.md`                | All model calls go through one gateway (rate limit, retry, fallback, budget, semaphore, cache) |
| `0006-checkpointing.md`              | LangGraph Postgres checkpointer for durable, resumable runs                                    |
| `0007-modular-monolith.md`           | Two deploy units (API + Worker); split into ingestion/eval workers only at scale               |
| `0008-contradictions-first-class.md` | Conflicting values are recorded, never silently resolved                                       |
| `0009-citation-validator-gate.md`    | A report cannot persist until every citation resolves to a stored evidence span                |

---

## 24. Open technical questions

- Reranker choice. (The embedding model and dimension were settled in Phase 7:
  `nomic-embed-text`, 768 dimensions; ADR 0012.)
- Web-search vendor (Tavily / Exa / Brave) and its rate/cost envelope.
- Celery vs ARQ for the queue (both fit; ARQ is lighter, Celery has more
  tooling).
- pgvector index type (HNSW vs IVFFlat) given expected chunk volume.
- Whether the knowledge graph stays a projection of `claims` or gets a dedicated
  store later.
- Provider assignment per role once cost/latency are measured.

---

## 25. Glossary

| Term                                   | Plain meaning                                                                            |
| -------------------------------------- | ---------------------------------------------------------------------------------------- |
| **Frontend / Backend**                 | Frontend = the website in your browser. Backend = the servers and databases behind it.   |
| **API**                                | How software talks to software; here also "the front counter" that receives requests.    |
| **Gateway**                            | A single controlled entry point that everything of a kind must pass through.             |
| **Agent**                              | One AI worker with one job. Multi-agent = a team of them.                                |
| **LLM**                                | Large Language Model, the AI that reads and writes text.                                 |
| **Token**                              | A chunk of text (~3/4 of a word), the unit AI models are billed in.                      |
| **Prompt**                             | The instructions + context given to an AI model for one call.                            |
| **RAG**                                | "Retrieve then generate", look things up in real sources before answering.               |
| **Orchestration**                      | Coordinating many steps/workers in the right order, with loops and retries.              |
| **LangGraph / LlamaIndex / LangChain** | Project manager / librarian / universal adapter (see Section 5).                         |
| **Node (in the graph)**                | One step in the workflow flowchart.                                                      |
| **State / clipboard**                  | The shared data every workflow step reads and writes.                                    |
| **Checkpoint**                         | A saved snapshot of workflow progress, for resuming after a crash.                       |
| **Queue / Worker**                     | Drop off a job, get a ticket; a background program does the slow work.                   |
| **SSE**                                | Server-Sent Events, a one-way live feed from server to browser.                          |
| **Postgres / system of record**        | The authoritative database; the single source of truth.                                  |
| **pgvector / vector search**           | "Search by meaning" stored inside Postgres.                                              |
| **Embedding**                          | Numbers representing text meaning, so meanings can be compared.                          |
| **BM25 / full-text / tsv**             | Classic "search by exact keywords".                                                      |
| **Hybrid retrieval**                   | Keyword + meaning search combined, then re-ranked.                                       |
| **Reranking / cross-encoder**          | A smarter second pass that re-sorts results by true relevance.                           |
| **Chunk**                              | A slice of a document sized for search and retrieval.                                    |
| **Knowledge graph**                    | A map of entities and their relationships.                                               |
| **Object storage / S3 / MinIO**        | A cheap warehouse for big files, separate from the database.                             |
| **Redis**                              | A very fast store used as the queue, cache, rate-limiter, and locks.                     |
| **Rate limiting / token bucket**       | Capping how many requests are allowed in a window.                                       |
| **Backpressure**                       | When busy, new work waits in line instead of overwhelming the system.                    |
| **Concurrency / semaphore**            | How many things run at once / the counter that enforces the limit.                       |
| **Idempotent**                         | Safe to repeat, running a step twice equals running it once.                             |
| **Content hash / fingerprint**         | A short code derived from content; identical content → identical code.                   |
| **Canonicalization**                   | Reducing many equivalent forms (of a URL) to one standard form.                          |
| **Backoff / jitter**                   | Waiting longer between retries, plus a random offset so retries don't sync up.           |
| **Circuit breaker**                    | Stop calling a failing dependency for a while instead of hammering it.                   |
| **Dead-letter queue**                  | Where jobs go after all retries fail, for later inspection.                              |
| **Prompt injection**                   | Fetched text that tries to hijack the AI's instructions; blocked by treating it as data. |
| **SSRF**                               | Tricking the server into calling a private internal address; blocked by design.          |
| **SAST**                               | Static analysis that scans source code for security bugs.                                |
| **Observability / trace / span**       | Seeing what happened / the record of one run / one step within it.                       |
| **P50 / P95 / P99**                    | The typical time / slowest 1-in-20 / slowest 1-in-100.                                   |
| **CI/CD**                              | Automated pipelines that test every change and deploy the good ones.                     |
| **IaC / Terraform**                    | Infrastructure as Code, cloud setup written in reviewable text files.                    |
| **Container / Docker / image**         | A standard box holding an app + its dependencies; an "image" is the box's template.      |
| **Kubernetes / ECS / Fargate**         | Systems that run and manage many containers in the cloud.                                |
| **Modular monolith**                   | One codebase in clean sections, simpler than many micro-services.                        |
| **Migration**                          | A versioned change to the database structure.                                            |
| **Pydantic**                           | A Python library that forces data into a defined shape and rejects malformed input.      |
| **Confidence score**                   | A 0-1 number for how sure the system is about a claim.                                   |
| **Citation**                           | The `[n]` marker linking a report statement to its source and supporting quote.          |
