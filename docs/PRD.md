# Aether Research: Product Requirements Document (PRD)

|                     |                                                                                           |
| ------------------- | ----------------------------------------------------------------------------------------- |
| **Working name**    | Aether Research                                                                           |
| **Document status** | Draft                                                                                     |
| **Version**         | 1.0                                                                                       |
| **Last updated**    | 2026-09-05                                                                                |
| **Owner**           | Product                                                                                   |
| **Related**         | [`TDD.md`](TDD.md), `CHANGELOG.md`, `architecture.md`, `threat-model.md`, `evaluation.md` |

---

## Document control

> **In plain terms:** this file (`docs/PRD.md`) is the **single master**: always
> the current truth. It is versioned only when a real product decision changes,
> not for wording, formatting, or added guidance. Those edits just update this
> file in place.

- **Master (this file):** living document, always current. Rendered to
  `docs/PRD.docx`.
- **What gets a new version (decisions only):**
  1. a change to product scope or direction,
  2. adding, changing, or removing a requirement (FR-*, a product mode, a
     non-functional target, an evaluation metric, a roadmap commitment),
  3. a decision that resolves or reopens one of the open questions.
- **What does NOT get a version:** wording and typography, plain-language
  rewrites, added examples or diagrams, "how to read" / guidance sections,
  formatting, link fixes, and document tooling. These edits update the master
  (and `PRD.docx`) directly, with only the `Last updated` line touched.
- **When a version IS cut:** bump `Version`, add a Change Log entry (Added /
  Changed / Deferred / Replaced / Removed, each with a reason), regenerate
  `PRD.docx`, and write the snapshot pair `docs/versions/PRD-v<x.y>.md` + `.docx`.
- **Combined index:** `docs/CHANGELOG.md` lists every decision version of the PRD
  and TDD.
- **Versioning:** `MAJOR.MINOR`. MINOR = a requirement added or clarified within
  the same product direction. MAJOR = a change in product direction, a dropped
  commitment, or an incompatible restructure.

### Version history

| Version | Date       | Status      | Summary                                   |
| ------- | ---------- | ----------- | ----------------------------------------- |
| 1.0     | 2026-09-05 | **Current** | Initial PRD from the staged specification |

### Change log

Newest first. Only decision versions are listed here.

#### v1.0 (2026-09-05): Initial PRD

- **Added**: first full PRD from the staged specification: summary and "what
  this is not"; problem statement; goals / non-goals; target users and five
  personas; the three product modes (Quick / Deep / Conversational); key user
  flows; functional requirements FR-1 to FR-10 (auth, research creation, query
  decomposition, source discovery, source extraction, evidence extraction,
  contradiction detection, the bounded research loop, final synthesis, research
  history); non-functional requirements (reliability, performance targets,
  99.5% availability, security); data and sources; the evaluation system and
  success metrics; the 10-phase release roadmap; risks and mitigations; open
  questions; glossary.
- **Deferred (still open):** source connectors beyond Web / SEC / arXiv / GitHub
  / uploaded PDFs (other government APIs, news/RSS feeds, internal
  knowledge-base connectors); non-English research corpora; mobile apps;
  real-time collaborative report editing; fine-tuned / self-hosted frontier
  models (local Ollama kept for comparison only).
  _Reason:_ keep v1 scope shippable; these are additive and not on the critical
  path.
- **Open decisions (unresolved):** LLM provider per role; web-search vendor
  (Tavily / Exa / Brave); deployment host/region and managed-vs-self database;
  scope of the first build increment; embedding model and size.

> Editorial and formatting revisions since v1.0 (a plain-language layer for
> non-technical readers, this document-control section, and a styling cleanup)
> are maintained in place and are **not** separately versioned.

---

## How to read this document

This PRD is written for a mixed audience.

- **If you are non-technical:** read Section 0 (Plain-English overview), Section 2 (Problem),
  Section 3 (Goals), Section 5 (Product modes), Section 6 (User flows), and Section 12 (Risks). Every other
  section opens with an indented **"In plain terms"** paragraph you can read and
  then skip the details underneath.
- **If you are technical:** Section 7 (Functional requirements), Section 8 (Non-functional
  requirements), Section 9 (Data), and Section 10 (Evaluation) are the contract. The
  [`TDD.md`](TDD.md) has the implementation design.
- **Jargon** is expanded in Section 14 (Glossary).

---

## 0. Plain-English overview

**What it is.** Aether Research is a tool where you type one complex research
question and get back a well-organised, fully-sourced report, the kind of report
a team of analysts would take days to produce.

**The everyday version of the problem.** Imagine you have been asked whether your
company should enter a new market. To answer well you would: search the web
dozens of times, open dozens of tabs, read company filings, skim research papers,
check what engineers are actually building, write down which fact came from
where, notice when two sources disagree, and then write a report your boss can
trust. This is slow, and by the end nobody can easily check where each statement
came from.

**How Aether helps.** It behaves like a small research team:

1. A **Planner** breaks your question into smaller questions.
2. Several **Researchers** go after those smaller questions **at the same time**.
3. An **Evidence step** pulls out the exact sentence that supports each finding
   and records its source.
4. A **Verification step** checks each fact against more than one source.
5. A **Contradiction step** flags disagreements between sources instead of hiding
   them.
6. A **Critic** decides whether the research is thorough enough; if not, it sends
   the team back for more (up to 4 times).
7. A **Writer** produces the final report.
8. A **Fact-checker** blocks the report from being saved unless every statement
   has a real source attached.

**What you receive.** A report with an executive summary, key findings, detailed
analysis, competitive landscape, evidence, contradictions, a confidence
assessment, recommendations, and references. **Every important claim is clickable
back to its source and its supporting quote**, and each claim carries a
**confidence score** from 0 to 1.

**Three ways to use it.** _Quick_ (a short answer in seconds), _Deep_ (the full
report in minutes), and _Conversational_ (ask a follow-up like "go deeper on
competitor X" and it builds on what it already found).

**Why the engineering matters.** The hard part is not connecting an AI to a
search box. The value is everything around it: the job keeps running if a server
restarts, it never exceeds a set budget of time and money, it treats web content
as untrusted, it continuously measures its own accuracy, and it is built to be
deployed and operated as a real product.

---

## 1. Summary

Aether Research is a production-grade, multi-agent research system. A user enters
a complex question; the system decomposes it into sub-questions, researches
multiple live sources in parallel, extracts claims with evidence spans, verifies
claims across sources, detects contradictions, and generates a citation-grounded
report with full traceability.

**One-line pitch:** A production-grade multi-agent research system that
decomposes complex questions, searches the web and private knowledge sources,
performs parallel research, verifies evidence and contradictions, and generates
citation-grounded reports with full traceability.

### 1.1 What this is not

- Not "a chatbot that searches Google."
- Not a single-shot question-answering endpoint.
- Not a static dataset product. There is **no prebuilt dataset**; every research
  task dynamically generates its own corpus from live sources.

### 1.2 What makes it different

> **In plain terms:** a normal search assistant gives you one quick answer and
> forgets everything. Aether runs a whole workflow, keeps every source and every
> fact it found, shows its confidence, points out disagreements, grades its own
> quality, and lets you pick up where you left off.

| Property   | Typical search assistant     | Aether Research                                                                |
| ---------- | ---------------------------- | ------------------------------------------------------------------------------ |
| Execution  | Synchronous request/response | Long-running, stateful, resumable workflow                                     |
| Agents     | One                          | Planner + parallel researchers + verifier + critic + synthesizer               |
| State      | Forgotten after the answer   | Persisted (database + save-points)                                             |
| Claims     | Unattributed prose           | Every material claim gets a supporting quote, a source, and a confidence score |
| Conflicts  | Silently resolved            | Contradictions recorded with a likely reason                                   |
| Quality    | Unmeasured                   | First-class evaluation surface + automated quality gate                        |
| Follow-ups | Restart from zero            | Reuse prior research                                                           |

---

## 2. Problem statement

> **In plain terms:** serious research is many hours of searching, reading,
> note-taking, and cross-checking. The finished document usually can't be easily
> audited afterwards, because the link from each sentence back to its proof is
> gone.

Knowledge workers performing serious research (market analysis, competitive
intelligence, technical due diligence, literature review) currently:

1. Run many manual searches and open dozens of tabs.
2. Read and cross-reference sources by hand.
3. Manually track which claim came from which source.
4. Miss contradictions between sources, or resolve them without noticing.
5. Produce reports where the chain from claim to evidence to source is lost.
6. Cannot cheaply re-run or extend the research later.

General-purpose chat assistants help with step 1 but collapse steps 2-6 into a
single opaque generation with weak or fabricated citations.

---

## 3. Goals and non-goals

### 3.1 Goals

- **G1**: Break a complex question into a validated set of prioritised
  sub-questions.
- **G2**: Research sub-questions **in parallel** across web, SEC, arXiv, GitHub
  and uploaded documents.
- **G3**: Extract atomic claims, each with a verbatim supporting quote, a
  source, and a calibrated confidence score.
- **G4**: Verify claims across independent sources and surface contradictions
  explicitly.
- **G5**: Generate a structured report where every material factual claim has a
  working citation.
- **G6**: Run as a durable, resumable job with live streaming progress.
- **G7**: Support quick, deep, and conversational research modes.
- **G8**: Measure research quality continuously and block releases that regress
  it.
- **G9**: Be deployable and operable: monitoring, rate limiting, caching, cost
  controls, infrastructure-as-code.

### 3.2 Non-goals (v1)

- No fine-tuned or self-hosted frontier models (a local option exists for
  comparison only).
- No browser automation / logged-in scraping of paywalled sources.
- No real-time collaborative editing of reports.
- No mobile apps (responsive web only).
- No non-English research corpora (detected and deprioritised, not fully
  supported).
- Source connectors beyond Web / SEC / arXiv / GitHub / uploaded PDFs are
  deferred (other government APIs, news feeds, internal knowledge bases).

---

## 4. Target users

> **In plain terms:** people who have to produce trustworthy research and answer
> for it later (analysts, engineers, product managers, founders, consultants),
> plus students doing serious research.

### 4.1 Primary

Researchers, engineers, analysts, product managers, founders, consultants, and
other technical professionals who perform complex, multi-source research and need
defensible, cited output.

### 4.2 Secondary

Students and knowledge workers performing complex research.

### 4.3 Personas

| Persona                           | Need                                                                     | Key mode              |
| --------------------------------- | ------------------------------------------------------------------------ | --------------------- |
| **Corporate-development analyst** | "Should we enter market X?", full landscape with financials              | Deep                  |
| **Founder / strategist**          | Fast read on a trend before a meeting                                    | Quick                 |
| **Technical due-diligence lead**  | Verify a vendor's claims against official filings and real code activity | Deep + Conversational |
| **Researcher / PhD student**      | Literature scan across papers with contradictions surfaced               | Deep                  |
| **Consultant**                    | A reusable, cited evidence base a client can audit                       | Deep + history        |

---

## 5. Product modes

The product ships **all three** modes; it is not one mode.

### 5.1 Quick Research

- **Target:** typical answer in well under 30 seconds.
- **Use:** "What are the biggest trends in AI agents?"
- **Pipeline:** Planner → Search → Rank → Synthesize.
- **Output:** a short synthesised answer with inline citations and a source list.
- No critic loop, no contradiction pass, a single round of retrieval.

### 5.2 Deep Research

- **Target:** typical report in 1-3 minutes; up to ~5 for the largest runs.
- **Pipeline:** Planner → parallel researchers → iterative retrieval →
  verification → critique → additional research → synthesis.
- **Output:** a full **Research Report** (see Section 7, FR-9).

### 5.3 Conversational Research

> **In plain terms:** you already have a report; now you want to pull one thread
> further without paying for the whole thing again.

- The user asks a follow-up such as "Go deeper on competitor X."
- The system **reuses prior research** (sources, documents, claims, evidence,
  contradictions) instead of starting from zero.
- Implemented as a new research run linked to a parent run; the planner is seeded
  with the parent run's evidence base and only researches the delta.

---

## 6. Key user flows

### 6.1 Create and run research

> **In plain terms:** you fill in a form and press start. You immediately get a
> "we're on it" response and a live status feed, you are not left staring at a
> spinner for five minutes. When it's done you open the report and can click any
> statement to see its source.

1. User signs in.
2. User opens **New Research** and supplies: question, mode, depth, optional
   domains, optional date range, optional uploaded documents.
3. The app sends the request; the server validates it, creates a research
   record, puts the job in a queue, and immediately replies "accepted" with a
   job id.
4. A background worker picks up the job and starts the workflow.
5. The planner splits the question; researchers work in parallel; evidence is
   extracted; the critic checks whether it is thorough enough; missing
   information triggers another bounded round; the writer produces the report;
   the fact-checker validates every citation; the report is saved.
6. Throughout, a live activity feed updates in the browser.
7. The user opens the finished report and clicks any citation to see the source
   and the exact supporting quote.

### 6.2 Follow-up (conversational)

1. From a completed report, the user types "Go deeper on competitor X."
2. A follow-up run is created that references the original.
3. The planner receives the original evidence base and produces only new
   sub-questions.
4. New evidence is merged in; a revised section (or a new report) is produced.

### 6.3 Review history

1. User opens **History**.
2. User reopens any prior research: the question, every run, all sources, all
   claims, all citations, the full step-by-step activity trace, and the final
   report are all still there.

---

## 7. Functional requirements

> **In plain terms:** this section is the checklist of things the product must
> actually do. FR = "Functional Requirement". Each one is testable.

### FR-1: Authentication

Users can register, log in, log out, and manage sessions.

- Stack: **Auth.js + PostgreSQL**: chosen deliberately so the team owns and
  understands the login system rather than renting it.
- Session management: a session list and a "sign out this device" control in
  `/settings`.
- Every research object is owned by a user; the system checks ownership on every
  read and write (you can only see your own research).

### FR-2: Research creation

The user supplies:

| Field              | Required   | Notes                                         |
| ------------------ | ---------- | --------------------------------------------- |
| Research question  | Yes        | Free text                                     |
| Research mode      | Yes        | `quick` \| `deep` \| `conversational`         |
| Depth              | Yes (deep) | Bounded by the hard limits in FR-8            |
| Domains            | No         | e.g. company websites, SEC, arXiv, GitHub     |
| Date range         | No         | e.g. "last 12 months"                         |
| Uploaded documents | No         | PDFs; parsed and added to the research corpus |

Example:

```
Question:   Compare AI inference infrastructure providers.
Mode:       Deep
Time range: Last 12 months
Domains:    company websites, SEC, arXiv, GitHub
```

### FR-3: Query decomposition

> **In plain terms:** the Planner must return a clean, structured list of
> sub-questions in a fixed format, not a free-form paragraph. If the AI's output
> doesn't fit the format, it is rejected and retried.

The planner transforms a complex question into structured subtasks. Output is
**schema-validated** (via Pydantic models); arbitrary AI output is rejected.

```json
{
  "research_goal": "...",
  "subtasks": [
    { "id": "market", "question": "...", "priority": "high" },
    { "id": "competitors", "question": "...", "priority": "high" },
    { "id": "technology", "question": "...", "priority": "medium" }
  ]
}
```

Standard decomposition dimensions for market/investment questions: market,
competitor, technology, pricing, financial, recent news, risk.

### FR-4: Source discovery

**v1 connectors:** Web Search, SEC EDGAR API, arXiv API, GitHub API, and
uploaded PDFs.

- **SEC EDGAR** (the US regulator's public database): official company filings,
  filing history, and extracted financial figures; updated throughout the day.
- **arXiv:** open-access research papers.
- **GitHub:** open-source project activity (a signal of what is actually being
  built).
- **Deferred:** other government APIs, news feeds, internal knowledge base
  connectors.

### FR-5: Source extraction

Each discovered source becomes a record:

```
Source
 ├── title
 ├── url
 ├── publisher
 ├── published_at        (when the source was published)
 ├── accessed_at         (when we fetched it)
 ├── content             (the cleaned text)
 ├── content_hash        (a fingerprint, so duplicates are detected)
 ├── source_type         (web | sec | arxiv | github | upload)
 └── credibility_metadata
```

The original source, the cleaned content, and the fingerprint are all stored.

### FR-6: Evidence extraction

> **In plain terms:** for every finding, keep the exact sentence that proves it,
> where it came from, how confident we are, when it was found, and which AI
> worker found it.

From documents, the system extracts:

```
Claim         : a single, normalised statement
Evidence span : the verbatim text that supports it
Source        : a working reference
Confidence    : a calibrated score from 0 to 1
Timestamp     : when it was extracted
Agent         : which AI worker / model produced it
```

Example:

```
Claim:      Company X expanded inference capacity by 40%.
Evidence:   "...we increased our inference fleet by roughly 40% quarter over quarter..."
Source:     https://example.com/report
Confidence: 0.92
```

### FR-7: Contradiction detection

> **In plain terms:** if one source says revenue was $200M and another says
> $180M, the system must not quietly pick one. It records both, and its best
> guess at why they differ.

The system **must not silently choose one** value when independent sources
disagree. It records:

```
Contradiction detected
Claim:         Revenue (for a given fiscal reference)
Source A:      $200M
Source B:      $180M
Likely reason: Different fiscal periods / restated figures
Resolution:    unresolved | resolved_a | resolved_b | both_valid_in_context
```

### FR-8: Research loop

> **In plain terms:** after each round, a Critic asks "is this good enough?" If
> not, it sends the team back for another round, but there are hard ceilings so
> the system can never run forever or run up an unbounded bill.

The critic determines whether the evidence is sufficient per sub-question
(coverage, confidence, contradiction resolution). If not, it routes the missing
information back to the planner, which issues new targeted searches.

**Hard limits (per run), prevent runaway agents:**

| Limit            | Value     | Why                         |
| ---------------- | --------- | --------------------------- |
| `max_iterations` | 4         | cap the re-research loop    |
| `max_sources`    | 50        | cap how much is fetched     |
| `max_cost`       | $2        | cap the money spent per run |
| `max_runtime`    | 5 minutes | cap wall-clock time         |

When any limit is hit, the run proceeds to synthesis with an explicit coverage
caveat in the report.

### FR-9: Final synthesis

The report contains, in order:

1. Executive Summary
2. Key Findings
3. Detailed Analysis
4. Competitive Landscape _(where applicable)_
5. Evidence
6. Contradictions
7. Confidence Assessment
8. Recommendations
9. References

Every important factual claim carries a source citation. Citations appear inline
as `[n]` and resolve to a numbered source list and the exact supporting quote.

### FR-10: Research history

The system stores, per research:

```
Research
 ├── runs             (each execution, including follow-ups)
 ├── queries          (what was asked)
 ├── sources          (everything discovered)
 ├── claims           (every extracted statement)
 ├── citations        (claim → source links)
 ├── agent traces     (the full step-by-step record)
 └── final report
```

The user can reopen any research later and inspect every layer.

---

## 8. Non-functional requirements

> **In plain terms:** FRs are _what it does_; NFRs are _how well it must do it_:
> speed, reliability, uptime, and safety.

### 8.1 Reliability

- Retry temporary failures automatically, with increasing waits between tries.
- Recover interrupted jobs (server crash, redeploy) from the last save-point.
- Idempotent ingestion, re-processing the same document is safe and produces no
  duplicates.
- Workflow progress is saved continuously, not held only in memory.

### 8.2 Performance (project targets, not third-party claims)

| Mode           | Target                                  |
| -------------- | --------------------------------------- |
| Quick research | 95% of runs finish in under 30 seconds  |
| Deep research  | 95% of runs finish in under 180 seconds |

### 8.3 Availability

- 99.5% uptime for the deployed demo (roughly no more than ~3.5 hours down per
  month).

### 8.4 Security

> **In plain terms:** keep secrets on the server, check who is asking, limit how
> much anyone can ask for, never trust text found on the internet as if it were
> an instruction, never let the server be tricked into calling internal
> addresses, handle uploaded files carefully, and scrub the final report.

- Secrets (API keys, passwords) live server-side only, never in the browser,
  never fed to an AI model.
- Authentication and authorisation on every research object.
- Rate limiting: per user, per endpoint, and per cost.
- Prompt-injection defence: text fetched from the web is treated as **data**,
  never as instructions to the AI.
- URL / domain allow-and-deny controls.
- SSRF protection: the server refuses to fetch internal or private network
  addresses, even after redirects.
- Malicious-document handling: sandboxed parsing, size and type limits, no macro
  execution.
- Output filtering: strip any injected instructions or secret-shaped text from
  the finished report.

Full detail: `threat-model.md`.

---

## 9. Data and sources

> **In plain terms:** there is no bought-in dataset. Each research job gathers
> its own material live, and everything it gathers is filed away so future jobs
> can reuse it.

- **Live internet data, generated per task.** No prebuilt dataset.
- Persistent research memory is a relational database (PostgreSQL) with tables
  for tasks, agent activity, sources, documents, claims, evidence, citations,
  contradictions, and reports (full schema in [`TDD.md`](TDD.md) Section 7).
- The evidence base accumulates across runs so later tasks reuse prior sources,
  claims and verified facts.
- Large files (PDFs, saved web pages, screenshots, parsed documents, generated
  reports, evaluation artifacts) live in object storage, not the database.

---

## 10. Evaluation and success metrics

> **In plain terms:** the product grades itself. There is a fixed set of test
> questions with known-good answers and sources. After each change the system
> answers all of them and gets a scorecard. A bad scorecard automatically blocks
> the change from shipping. We do not put numbers in the README until we have
> measured them.

Evaluation is a **first-class product surface** at `/evaluations`, not a
notebook.

### 10.1 Dashboard

**Research quality:** citation accuracy, citation completeness, retrieval recall,
claim correctness, groundedness.

**System:** P50 / P95 latency, cost per run, tool failure rate.

### 10.2 Evaluation dataset

- 100-300 research questions.
- Coverage: technology, business, finance, science, current events, competitive
  analysis, historical questions, multi-hop questions (needing several linked
  lookups), and contradictory-evidence cases.
- Each case:

```json
{
  "question": "...",
  "expected_topics": ["..."],
  "expected_sources": ["..."],
  "must_cite": true
}
```

### 10.3 Automated evaluation dimensions

| Layer          | Metrics                                                                             | Plain meaning                                                                  |
| -------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Retrieval      | Recall@K, Precision@K, MRR, NDCG                                                    | did it find the right sources, and rank them well?                             |
| Generation     | answer correctness, faithfulness, groundedness, citation precision, citation recall | is the writing accurate and true to the evidence, and are the citations right? |
| Agents         | task success, tool selection, planning accuracy, unnecessary calls, recovery rate   | did the workers do their jobs efficiently and recover from errors?             |
| Infrastructure | P50, P95, P99, throughput, cost, failure rate                                       | is it fast, cheap, and stable enough?                                          |

### 10.4 Release gate

Every code change runs unit + integration + agent + RAG tests + the evaluation
benchmark. CI **fails** when a gated metric drops below threshold, for example,
citation correctness below 90%.

---

## 11. Release roadmap

> **In plain terms:** build in slices, each adding one capability. Do not attempt
> a later slice until the earlier one works. The first slice is a good-looking
> website running on fake data, so the experience can be judged before the engine
> is built.

| Phase                         | Scope                                                                       | Deliverable                     |
| ----------------------------- | --------------------------------------------------------------------------- | ------------------------------- |
| **0. Product prototype**      | Website + fake research data + fake agents + fake activity feed + report UI | A convincing clickable product  |
| **1. Basic backend**          | Accounts, database, create/read research                                    | Authenticated CRUD              |
| **2. First AI**               | One Planner + Researcher + Synthesizer; a single straight-line path         | One end-to-end AI answer        |
| **3. Web research**           | Real search, fetch, parse, source database, citations                       | Cited answers from the live web |
| **4. RAG**                    | Indexing and smart retrieval over collected documents                       | Retrieval-grounded answers      |
| **5. Multi-agent**            | Add Critic + Verifier; run researchers in parallel                          | Parallel, verified research     |
| **6. Durable execution**      | Save-points, queue, background workers, resume, retry                       | Resumable long-running runs     |
| **7. Evaluation**             | Test set, scoring runner, dashboard, release gate                           | `/evaluations` + automated gate |
| **8. Production engineering** | Monitoring, rate limiting, caching, load testing, security, cost controls   | An operable service             |
| **9. Deployment**             | Cloud hosting, infrastructure-as-code, automated deploys, monitoring        | Live demo at 99.5% uptime       |

Sequencing rules: mock-first frontend before backend; one AI and one linear path
before multi-agent; RAG before the critic loop; durability added **after** the
agents work synchronously; evaluation before production hardening.

---

## 12. Risks and mitigations

| Risk (plain description)                                       | Impact | Mitigation                                                                                                      |
| -------------------------------------------------------------- | ------ | --------------------------------------------------------------------------------------------------------------- |
| The AI loop runs too long or spends too much                   | High   | Hard limits (FR-8); a gateway that budgets tokens and caps concurrency; a per-run search budget                 |
| A web page tricks the AI with hidden instructions              | High   | Treat fetched content as data only; wrap it in delimiters; filter the output; give agents a tiny fixed tool set |
| The server is tricked into calling an internal address         | High   | Block private/internal addresses on every fetch, including after redirects; domain allow/deny lists             |
| The report cites sources that don't actually support the claim | High   | A Citation Validator gate, the report cannot be saved until every citation resolves to a stored quote           |
| Ten copies of one news story look like ten independent sources | Medium | De-duplicate by canonical URL, content fingerprint, and meaning; a cluster counts once                          |
| An outside API is rate-limited or down                         | Medium | Retries, caching, graceful degradation, a dead-letter queue for failed jobs                                     |
| Deep runs miss the speed target                                | Medium | Parallel researchers, cheap models for easy steps, caching, bounded loops                                       |
| Over-reliance on a single AI provider                          | Medium | A provider-agnostic model layer plus a local option                                                             |
| The system "learns the test"                                   | Medium | A held-out split, periodic dataset refresh, diverse categories                                                  |

---

## 13. Open questions

- Final AI provider assignment per role (working defaults: Planner and
  Synthesizer on Anthropic; Researcher and Critic on OpenAI; local development on
  Ollama).
- Web-search provider (Tavily / Exa / Brave).
- Deployment host and region; managed database vs self-managed.
- Scope of the very first build increment: full stack, or the backend workflow
  first.
- Embedding model and size (affects the "search by meaning" index).

---

## 14. Glossary

| Term                                            | Plain meaning                                                                                           |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| **Agent**                                       | A single AI worker with one job. "Multi-agent" = a team of them.                                        |
| **LLM**                                         | Large Language Model, the AI that reads and writes text.                                                |
| **RAG**                                         | "Look it up in real sources, then answer" instead of answering from memory.                             |
| **Planner / Researcher / Critic / Synthesizer** | The named AI workers: splits the question / gathers info / judges sufficiency / writes the report.      |
| **Run**                                         | One execution of the research workflow for a question (follow-ups are linked child runs).               |
| **Subtask**                                     | One prioritised sub-question the Planner produces.                                                      |
| **Source**                                      | A discovered document reference (web page, filing, paper, repo, upload).                                |
| **Document**                                    | The fetched and cleaned-up content of a source.                                                         |
| **Chunk**                                       | A slice of a document, sized so it can be searched and retrieved.                                       |
| **Claim**                                       | A single normalised statement extracted from a document.                                                |
| **Evidence span**                               | The exact verbatim text that supports (or refutes) a claim.                                             |
| **Citation**                                    | The `[n]` marker in the report linking a statement to its source and quote.                             |
| **Contradiction**                               | Two claims about the same thing with conflicting values.                                                |
| **Confidence score**                            | A 0-1 number for how sure the system is about a claim.                                                  |
| **Checkpoint / save-point**                     | A saved snapshot of workflow progress, so a crash can resume.                                           |
| **Queue / Worker**                              | Drop off a job, get a ticket; a background program does the slow work.                                  |
| **Streaming / SSE**                             | A live one-way progress feed from server to browser.                                                    |
| **Database / PostgreSQL**                       | The master store of authoritative records.                                                              |
| **Vector search / pgvector**                    | "Search by meaning", matches related text even when the words differ.                                   |
| **BM25 / full-text search**                     | Classic "search by exact keywords".                                                                     |
| **Hybrid retrieval**                            | Keyword search and meaning search combined, then re-ranked.                                             |
| **Reranking**                                   | A smarter second pass that re-sorts results by true relevance.                                          |
| **Embedding**                                   | Numbers that represent the meaning of text, so a computer can compare meanings.                         |
| **Knowledge graph**                             | A map of entities (companies, people, products) and their relationships.                                |
| **Object storage / S3**                         | A cheap warehouse for large files, separate from the database.                                          |
| **Redis**                                       | A very fast store used here as the job queue, cache, and rate-limiter.                                  |
| **Rate limiting**                               | Capping how many requests a user or the system handles at once.                                         |
| **Backpressure**                                | When busy, new work waits in line instead of overwhelming the system.                                   |
| **Idempotent**                                  | Safe to repeat, doing a step twice is the same as doing it once.                                        |
| **Prompt injection**                            | Web-page text that tries to hijack the AI's instructions; blocked by treating all fetched text as data. |
| **SSRF**                                        | Tricking the server into fetching a private internal address; blocked by design.                        |
| **Latency / P50 / P95 / P99**                   | Response time / the typical time / the slowest 1-in-20 / the slowest 1-in-100.                          |
| **CI/CD**                                       | Automated pipelines that test every change and deploy the good ones.                                    |
| **IaC**                                         | Infrastructure as Code, cloud setup written in reviewable text files.                                   |
| **Container / Docker**                          | A standard box holding an app plus everything it needs to run identically anywhere.                     |
| **Modular monolith**                            | One codebase kept in clean sections, simpler than many tiny services.                                   |
| **Pydantic**                                    | A Python library that forces data into a defined shape and rejects malformed input.                     |
