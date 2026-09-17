# ADR 0016: The evidence tables are a projection of the checkpoint, not a second source of truth

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

After Phase 10 a research run produces everything the product promises - claims,
verbatim evidence spans with offsets, contradictions, the sources behind them -
and none of it is readable. LangGraph stores the state as one serialised blob
per step in the checkpoint tables (ADR 0014), addressed by thread id. That shape
is right for the thing it exists for: resuming a run at the node that had not
finished. It answers no other question. "Which claims rest on this source",
"which contradictions are still unresolved", "page two of the evidence" are all
scans of every blob a run ever wrote.

Meanwhile the schema has held `claims`, `evidence`, `contradictions` and
`sources` since Phase 3, and two endpoints have returned empty lists since
Phase 2. So the question is not whether to write the rows; it is what the rows
_are_ relative to the checkpoint, and that decides how they behave when a run
is resumed, retried, or fails halfway.

Three properties of the system force the answer:

**A run is resumed, not replayed.** The runner's promise is that calling it
again continues from the last checkpoint; nodes that finished are not called
again. So whatever writes the rows will be handed state it has already seen,
routinely, including after a crash between the write and the acknowledgement.

**Nothing may be written twice.** A claim counted twice is corroboration
invented, which is the failure mode this product is built to avoid.

**A person can change a row.** A contradiction can be resolved by an analyst.
That is a fact the system did not derive and cannot re-derive.

## Decision

### The relational rows are a projection, and the checkpoint stays authoritative

`app/evidence/projection.py` reads the graph's state and writes the entities
Phase 3 declared. Nothing reads them back into the graph: a resumed run reads
its checkpoint, exactly as before. The projection is the product's read model -
what the API serves, what Phase 12's report is assembled from, and what Phase
17 will measure.

Writing rows from inside a node was the alternative, and it fails the first
property above: a node that writes a row and then fails is re-run on resume, and
the write is not part of the checkpoint's transaction. Keeping the rows outside
the graph is what lets them be rewritten instead of reconciled.

### Every projected id is derived from what it describes

A claim's id is `uuid5` over the run and its normalized key (ADR 0015); a
contradiction's over its ordered pair; a claim-to-span link's over the claim and
the span; a dedup cluster's over the run and its primary source. Every write is
an upsert on that id, so projecting a run twice writes the same rows twice
rather than a second copy of everything. Idempotency is a property of the
identity scheme rather than a flag a caller has to remember.

Two columns are deliberately not overwritten by a re-projection:

- a claim's `first_seen_at` - when the run first believed something, which a
  re-projection does not change;
- a contradiction's `resolution` and `resolved_by` - the projection only ever
  writes `unresolved`, so overwriting would replace a person's judgement with
  the system's silence. FR-7 forbids silently resolving a conflict, and
  silently un-resolving one is the same failure inverted.

### Denormalised counts are recomputed, never incremented

`sources.claim_count` and a run's claim and contradiction counts are recomputed
from the rows beneath them in the same transaction. An increment is correct
exactly once; this code runs again by design, and a count that drifts from the
page under it is a number nobody can reconcile.

### Corroboration counts clusters, not rows

`app/evidence/dedup.py` groups a run's sources - identical content hash, then
canonical URL, then overlapping text - and a claim's `corroboration_count` is
the number of distinct clusters behind it. One wire story on four sites is one
source's word four times.

The rules run strongest first and a cluster records the weakest rule that
actually merged something in it, so "collapsed because the digests matched" and
"collapsed because the text overlapped" are distinguishable by a reader. Every
threshold is set to merge reluctantly: a false merge costs a claim corroboration
it deserved, a false split invents corroboration that never existed, and only
the second misleads.

The TDD specified embedding cosine for signal 3 and this implements trigram
containment (TDD 9.3 records the change). Embedding similarity is high for two
_different_ articles on the same subject - that is what the embedding is for -
so it merges independent accounts, which is the error that inflates confidence.

### A failed run is projected too

The runner hands the last checkpoint to the recorder on its way out of a
failure. Synthesis failing does not unfind the sources or unquote the spans, and
someone diagnosing the failure needs to see what the run had gathered. That
path swallows its own errors: the exception on its way up is why the run
stopped, and replacing it with a projection error - or with the database error
that may have been the original cause - would lose the diagnosis and change what
the worker retries. The success path does not swallow anything: a run whose
results could not be stored has not finished.

## Consequences

- The two stores are consistent only after a projection runs. A run in flight
  has rows from its last projected state, not its current one - which is what
  the live event stream (Phase 14) is for, and why the run's status, not its row
  count, is what says whether it is finished.
- Every future read model follows this shape. Phase 12's report rows and Phase
  17's trace rows are projections of the same state, with derived ids, and they
  inherit the same re-runnability for free.
- Deduplication compares the stored 280-character excerpt, so it catches a
  syndicated copy and does not catch a paraphrase. Widening it means loading
  document text, which would make a projection cost what the research cost.
- A column a person may edit has to be named in the upsert's exclusion list.
  That is a rule someone can forget when adding one; `resolution` has a test
  that fails if it is ever overwritten.
