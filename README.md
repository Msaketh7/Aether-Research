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

| Role on a human research team | The equivalent part of Aether |
|---|---|
| Lead analyst who breaks the big question into smaller ones and hands them out | **Planner** |
| Several junior analysts, each researching one sub-topic **at the same time** | **Researchers** (run in parallel) |
| Someone who highlights the exact sentence that proves each point and notes the source | **Evidence Extractor** |
| A checker who confirms each fact appears in more than one trustworthy place | **Verification Agent** |
| A checker who flags "these two sources disagree" instead of quietly picking one | **Contradiction Check** |
| A demanding manager who asks "is this good enough?" and sends people back for more | **Critic** |
| The writer who turns all the findings into a clean, structured report | **Synthesizer** |
| The fact-checker who makes sure every sentence in the report has a real source attached before it goes out | **Citation Validator** |

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

| Mode | Target speed | What it does |
|---|---|---|
| **Quick** | under ~30 seconds | Plan → search → rank → write a short cited answer |
| **Deep** | ~1-5 minutes | The full pipeline above: parallel research, verification, contradiction checks, a critique loop, then a full report |
| **Conversational** | follow-ups | "Go deeper on competitor X", reuses everything already gathered instead of starting over |

---

## Performance

> **In plain terms:** these are the goals for our own deployed demo, not promises
> about any outside service. The "Measured" column stays blank until we have run
> the real benchmark, we do not publish numbers we have not measured.

| Metric | Target | Measured |
|---|---|---|
| Quick research, P50 latency (typical time) |, | `[benchmark]` |
| Quick research, P95 latency (slowest 1 in 20) | < 30 s | `[benchmark]` |
| Deep research, P50 latency |, | `[benchmark]` |
| Deep research, P95 latency | < 180 s | `[benchmark]` |
| Cost per run |, | `[benchmark]` |
| Tool failure rate |, | `[benchmark]` |
| Availability (uptime) | 99.5% | `[benchmark]` |

---

## Evaluation

> **In plain terms:** we keep a fixed set of 100-300 test questions with known
> good answers and sources. After every change, the system answers all of them
> and is scored. If the score drops too low, the change is rejected
> automatically.

| Metric | What it means | Threshold | Measured |
|---|---|---|---|
| Citation accuracy | cited sources actually support the claim | ≥ 90% | `[benchmark]` |
| Citation completeness | important claims that have a citation |, | `[benchmark]` |
| Retrieval recall | share of the right sources it managed to find |, | `[benchmark]` |
| Claim correctness | claims that are factually right |, | `[benchmark]` |
| Groundedness / faithfulness | report stays true to the evidence, no invention |, | `[benchmark]` |
| Recall@K / Precision@K / MRR / NDCG | standard search-quality scores |, | `[benchmark]` |
| Agent task success / recovery rate | sub-tasks completed / failures recovered from |, | `[benchmark]` |

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

| Layer | Choice | Why (plain English) |
|---|---|---|
| Frontend | Next.js, TypeScript, Tailwind, shadcn/ui, TanStack Query, SSE | Builds the website, its styling, and the live progress feed |
| API | FastAPI (modular monolith) | The "front counter" that receives requests; one well-organised codebase, not a scatter of tiny services |
| Orchestration | **LangGraph** | Runs the multi-step agent workflow, remembers progress, handles the loop, resumes after a crash |
| Retrieval | **LlamaIndex** | Reads and indexes documents, then finds the relevant passages |
| Model/tool layer | **LangChain** | A common adapter so we can swap AI providers and tools without rewriting agents |
| System of record | PostgreSQL | The master database, the single source of truth |
| Vectors | pgvector (inside Postgres) | "Search by meaning" lives in the same database, one less moving part |
| Object storage | S3 | Cheap warehouse for big files (PDFs, saved pages, reports) |
| Cache / queue | Redis + Celery/ARQ | The ticket queue for jobs and a fast cache to avoid repeat work |
| Auth | Auth.js + PostgreSQL | Handles sign-up / login ourselves so we understand and control it |
| Observability | OpenTelemetry, Prometheus, Grafana, LangSmith | Tracing, metrics, dashboards, and AI-call inspection, so we can see what happened |
| Infra | Docker, AWS, Terraform, GitHub Actions | Packaging, cloud hosting, "infrastructure written as code", and automated deploys |

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

## Quick start

```bash
# 1. clone and configure
git clone <repo-url> aether-research && cd aether-research
cp .env.example .env          # add provider API keys (server-side only)

# 2. bring up Postgres + Redis + API + worker + web
docker compose up --build

# 3. run migrations and seed the eval dataset
make migrate
make seed-eval

# 4. open the app
open http://localhost:3000
```

---

## Build roadmap

> **In plain terms:** build it in slices. Each row adds one capability on top of
> the last. Nothing after row 0 is attempted until row 0 works.

| Phase | Scope | Status |
|---|---|---|
| 0 | Product prototype, website with fake data, so the experience is real before the engine exists | ☐ |
| 1 | Basic backend, accounts, database, create/read research | ☐ |
| 2 | First AI, one Planner + Researcher + Synthesizer, single straight-line path | ☐ |
| 3 | Web research, real searching, fetching, parsing, and citations | ☐ |
| 4 | RAG, indexing and smart retrieval over collected documents | ☐ |
| 5 | Multi-agent, add Critic + Verifier, run researchers in parallel | ☐ |
| 6 | Durable execution, save-points, queue, background workers, resume | ☐ |
| 7 | Evaluation, the test set, the scoreboard, the release gate | ☐ |
| 8 | Production engineering, monitoring, rate limits, caching, load tests, security | ☐ |
| 9 | Deployment, cloud hosting, infrastructure-as-code, automated deploys, monitoring | ☐ |

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

| Term | Plain meaning |
|---|---|
| **Agent** | A single AI worker with one job (e.g. the Planner). A "multi-agent" system is a team of them. |
| **LLM** | Large Language Model, the AI that reads and writes text (e.g. GPT, Claude). |
| **RAG** | Retrieval-Augmented Generation, "look it up in real sources, then answer", instead of answering from memory. |
| **Orchestration** | Coordinating the many steps and workers in the right order, with loops and retries. |
| **API** | The way two pieces of software talk to each other; here also the "front counter" that receives requests. |
| **Frontend / Backend** | Frontend = what you see in the browser. Backend = the servers and databases behind it. |
| **Queue / Worker** | You drop off a request and get a ticket (queue); a separate program (worker) does the slow job in the background. |
| **SSE (streaming)** | Server-Sent Events, a live one-way feed from server to browser, like a delivery tracker. |
| **Checkpoint** | A saved snapshot of progress, like a save point in a video game, so work can resume after a crash. |
| **Database / Postgres** | The master filing cabinet where the authoritative records live. |
| **Vector search / pgvector** | "Search by meaning", finds related text even when the words differ. |
| **BM25 / full-text search** | The classic "search by exact keywords" method. |
| **Hybrid retrieval** | Using keyword search and meaning search together, then re-ranking the combined results. |
| **Reranking** | A second, smarter pass that re-sorts search results by how relevant they really are. |
| **Embedding** | A list of numbers that represents the meaning of a piece of text, so a computer can compare meanings. |
| **Knowledge graph** | A map of things (companies, people, products) and how they relate. |
| **Object storage / S3** | A cheap warehouse for large files, separate from the database. |
| **Redis** | A very fast in-memory store used here as the job queue, cache, and rate-limiter. |
| **Rate limiting** | "Take a number", capping how many requests a user or the system handles at once. |
| **Backpressure** | When the system is busy, new work waits in line instead of overwhelming it. |
| **Concurrency** | How many things run at the same time. |
| **Idempotent** | Safe to repeat, doing the same step twice has the same effect as doing it once. |
| **Prompt injection** | A trick where text on a web page tries to give the AI new instructions; we treat all fetched text as data, never instructions. |
| **SSRF** | Server-Side Request Forgery, tricking our server into fetching a private internal address; blocked by design. |
| **Observability** | Being able to see what the system did, traces, metrics, dashboards, logs. |
| **P50 / P95 / P99 latency** | The typical time / the slowest 1-in-20 / the slowest 1-in-100. |
| **CI/CD** | Automated pipelines that test every change and deploy the good ones. |
| **IaC (Infrastructure as Code)** | Servers and cloud setup defined in text files so they are repeatable and reviewable. |
| **Container / Docker** | A standard box that holds an app plus everything it needs to run, identically everywhere. |
| **Modular monolith** | One codebase kept in clean sections, simpler than many tiny services, but still organised. |
| **Confidence score** | A 0-1 number saying how sure the system is about a claim. |
| **Citation** | The `[n]` marker in the report that links a statement to its source and exact supporting quote. |

## License

See [`LICENSE`](LICENSE).
