# Aether Research: Document Changelog

Combined version index for the two master design documents. Each master keeps its
own detailed change log; this file is the at-a-glance cross-reference.

| Document | Master (always current) | Word render | Versioned snapshots |
|---|---|---|---|
| Product Requirements | [`PRD.md`](PRD.md) | `PRD.docx` | `versions/PRD-v<x.y>.md` + `.docx` |
| Technical Design | [`TDD.md`](TDD.md) | `TDD.docx` | `versions/TDD-v<x.y>.md` + `.docx` |

## How versioning works

- **One master per document.** `docs/PRD.md` and `docs/TDD.md` are the living
  source of truth and always reflect the latest accepted decisions.
- **Versions track decisions, not edits.** A new version is cut only for:
  - an **architecture decision** (a new or changed ADR always counts),
  - a **requirement change** (adding, changing, or removing an FR, a product
    mode, a non-functional target, an evaluation metric, or a roadmap
    commitment), or
  - a **technical / system design decision** (the agent graph, the data schema,
    an API contract, a hard limit, a security control, the queue / durability /
    concurrency model, observability, CI/CD, or deployment topology).
- **Not versioned:** wording and typography, plain-language rewrites, added
  examples / diagrams / captions for readability, "how to read" and guidance
  sections, link fixes, and the document tooling. These edits update the master
  (and its `.docx`) in place; only the `Last updated` line moves.
- **When a version is cut:** bump `Version` in the master, add a Change Log entry
  (**Added / Changed / Deferred / Replaced / Removed**, each with a **reason**),
  regenerate the `.docx`, and write a frozen snapshot pair to `docs/versions/`
  (Markdown + Word) so the prior decision state can be retrieved exactly.
- **Word files are generated**, never hand-edited, via
  [`scripts/md-to-docx.js`](../scripts/md-to-docx.js) /
  [`scripts/build-docs.js`](../scripts/build-docs.js). Regenerate after any
  master edit, versioned or not.
- **Scheme:** `MAJOR.MINOR`. MINOR = a decision that is additive or clarifying
  within the same direction (a new ADR is at least MINOR). MAJOR = a change in
  direction, a dropped commitment, or an incompatible restructure.

## Regenerate the Word documents

```bash
node scripts/build-docs.js       # rebuilds every master and snapshot .docx
```

If `docx` is not installed in the repo, point Node at any `node_modules` that has
it: `NODE_PATH=/path/to/node_modules node scripts/build-docs.js`.

---

## PRD versions

| Version | Date | Status | Summary |
|---|---|---|---|
| 1.0 | 2026-09-05 | Current | Initial PRD from the staged specification: FR-1 to FR-10, 3 product modes, 10-phase roadmap, metrics, risks. |

## TDD versions

| Version | Date | Status | Summary |
|---|---|---|---|
| 1.0 | 2026-09-05 | Current | Initial TDD from the staged specification: topology, agent graph, ~20-table schema, RAG + web pipelines, durability, security, observability, evaluation, CI/CD, deployment, 9 ADRs. |

> The plain-language layer, the document-control sections, and a styling cleanup
> were applied after v1.0 as editorial revisions. They changed no requirement and
> no design decision, so they did not create new versions.

---

## Deferred / open items carried forward

Recorded at v1.0 of each document and still open. Tracked here so they are not
lost.

### Product (PRD)

| Item | Reason deferred | Status |
|---|---|---|
| Source connectors beyond Web / SEC / arXiv / GitHub / uploaded PDFs (other gov APIs, news/RSS, internal KB) | Keep v1 scope shippable; additive, not on the critical path | Open |
| Non-English research corpora | Detected and deprioritised for v1 | Open |
| Mobile apps | Responsive web only for v1 | Open |
| Real-time collaborative report editing | Not needed for the core value | Open |
| Fine-tuned / self-hosted frontier models | Local Ollama kept for comparison only | Open |
| LLM provider per role; web-search vendor; deploy host/region; first build increment; embedding model + size | Pending measurement / a product call | Open |

### Technical (TDD)

| Item | Reason deferred | Status |
|---|---|---|
| Split worker into dedicated ingestion / evaluation pools | Avoid premature infra; single worker suffices at demo scale | Open (ADR-0007) |
| Dedicated vector DB separate from Postgres | pgvector sufficient at v1 scale | Open (ADR-0003) |
| Standalone knowledge-graph store | Kept as a projection of `claims` for now | Open |
| Kafka for the queue | Redis + Celery/ARQ sufficient for v1 | Open |
| Embedding model + dimension; reranker; Celery vs ARQ; pgvector index type (HNSW vs IVFFlat) | Pending benchmarking | Open |
