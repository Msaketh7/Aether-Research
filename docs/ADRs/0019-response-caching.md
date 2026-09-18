# ADR 0019: What may be cached, keyed by content, and what may never be

- **Status:** Accepted
- **Date:** 2026-09-17

## Context

A research run repeats itself. Two researchers given overlapping subtasks
search for the same thing and fetch the same page; a document re-ingested for a
follow-up is re-embedded chunk by chunk; a page fetched for one run is fetched
again by the next. TDD 13 named the four caches that address this and the rule
that bounds them: never cache sensitive per-user information globally.

The risk in a cache is not the storage. It is a key that omits an input - and
then serves the answer to a different question - and it is the temptation to
cache the expensive thing rather than the deterministic thing. The most
expensive call the system makes is a model completion, and it is exactly the
one that must not be cached blindly.

## Decision

**A closed list of four namespaces**: a search provider's answer, a fetched
page, the article extracted from that page's HTML, and one text's embedding
vector. Each is cached because it is deterministic given its inputs _and_
expensive to repeat. Adding a fifth is an edit to an enum, which is where the
argument for it has to be made.

**Keys are content hashes of every input that decides the answer**, carrying a
namespace and a schema version. A search key includes its provider, so a
failover cannot serve Brave's results as Tavily's. A page key includes
`ignore_robots`, so a page fetched under an override is not served to a caller
that did not ask for one. Bumping `KEY_VERSION` invalidates everything in one
edit, which is the only invalidation that behaves during a half-finished
deployment.

**Nothing that belongs to a person has a namespace to live in.** Retrieval
results are not cached - freshness is the point and the corpus is the user's
(TDD 8.3). A run's claims, evidence and reports are not cached: they are rows,
and a cache in front of the system of record is a second source of truth. The
one namespace that touches user content is the embedding cache, keyed by the
hash of the exact prepared text, so a lookup requires already holding that
text; `CACHE_EMBEDDINGS=false` is offered for a deployment that would rather
not make that argument at all.

**Completions are not cached.** "This prompt is deterministic" is a claim about
a prompt that nothing in the codebase can check, and a wrong cache hit on a
synthesis is the most expensive mistake the system could make. The seam exists
in the gateway; the switch is not thrown.

**Caching and deduplication are separate promises.** Simultaneous identical
calls are coalesced in-process whether or not anything is stored, because "do
this once" and "remember the answer" are different guarantees and only the
second is an optimisation an operator might turn off. The scope is one process,
which is the scope that matters: the callers being deduplicated are the
researchers of one run, and a run has exactly one worker (ADR 0017).

**A cache hit is still a recorded call.** Tool and model records carry
`cache_hit`, because a run whose ledger simply lacks a call cannot be told from
one that never made it - and a saving nobody can see is a saving nobody
believes.

**A failure is a miss.** An unreachable backend, an entry from an older
encoding, a value over the size ceiling: each degrades to computing the value.
A research run must never fail because an optimisation was unavailable.

## Consequences

- Three of the six research tools are cached; SEC, arXiv and GitHub are not,
  because they are indexes a run is entitled to expect current.
- A fetched page is stored base64-encoded in JSON, so a ceiling applies
  (512 KiB of body). Larger pages are fetched every time, which is the correct
  trade for the pages nobody reads twice.
- Embeddings are cached per text, not per batch, so a document that shares
  chunks with one already ingested sends only the new ones.
- The in-memory backend is bounded and is what `APP_ENV=test` selects; the
  suite exercises every policy above the backend without Redis.
- Cache-hit rate becomes measurable in Phase 17 from the flags recorded here,
  and cost saved is reportable in Phase 18 rather than estimated.
