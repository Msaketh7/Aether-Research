"""Turning what the graph derived into what the product shows.

The research graph keeps its working memory in a LangGraph checkpoint: one
serialised blob per step, addressed by thread id, readable only by the graph.
That is the right shape for resuming a run and the wrong shape for every other
question the system is asked - which claims does this source support, which
contradictions are unresolved, show me page two - so this projects the state onto
the relational entities Phase 3 declared.

**It is a projection, not a second source of truth.** The checkpoint remains
what a resumed run reads; nothing here is read back into the graph. So running
it again is not a conflict to resolve, and every id it writes is derived from
what it describes - a claim's from the run and its normalized key, a span's from
the claim and the span, a contradiction's from its ordered pair. Projecting a
run twice writes the same rows twice. That happens routinely: a run that crashes
after synthesis resumes from its checkpoint, and the nodes that already ran are
not re-run, but everything they produced is projected again.

**A failed run still has evidence.** Synthesis failing does not unfind the
sources or unquote the spans, so the projection runs for a run that raised as
well as one that finished. What it cannot do is invent the parts that never ran:
a run with no claims projects no claims.

Three things are computed here rather than carried in state, because each is a
fact about the whole run that no single node could know:

* **Corroboration** is the number of distinct dedup clusters behind a claim, so
  the same wire story found on four sites counts once (``app.evidence.dedup``).
* **The subject and predicate** of a claim are the first two parts of its
  normalized key, which is where the normalizer put them.
* **A contradiction's sources** are the sources its two claims rest on - the
  claims know their spans, and the spans know where they came from.

**Nothing is resolved.** A contradiction is written ``unresolved`` with no
``resolved_by``, every time. FR-7 is not that conflicts are rare; it is that the
system never quietly picks a winner, and the place that would be tempted to is
exactly here, where both values are finally in one row together.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from app.agents.schemas import ClaimItem, ContradictionItem, EvidenceItem
from app.agents.state import ResearchState
from app.core.enums import AgentName, ClaimType, ContradictionResolution
from app.core.logging import get_logger
from app.evidence.dedup import cluster_sources
from app.evidence.repository import (
    ClaimRecord,
    ContradictionRecord,
    EvidenceRecord,
    EvidenceStore,
)

logger = get_logger(__name__)

#: Namespace for the id of a claim-to-span link. Distinct from the span's own id
#: in graph state: one span can be evidence for two claims, and the table's grain
#: is the link (see ``EvidenceRecord``).
_LINK_NAMESPACE = uuid.UUID("b4f2d9e7-1c86-4a35-9f0b-2e5d8c71a643")

#: What is written when a span comes from a checkpoint made before extraction
#: recorded the answering model. Not the role's configured model, which would
#: attribute the span to something that may never have produced it.
UNKNOWN_MODEL = "unknown"


def evidence_link_id(claim_id: uuid.UUID, evidence_id: uuid.UUID) -> uuid.UUID:
    """The row id for "this span is evidence for that claim"."""
    return uuid.uuid5(_LINK_NAMESPACE, f"{claim_id}:{evidence_id}")


@dataclass(frozen=True, slots=True)
class Projected:
    """What one projection wrote. Returned so a caller can log or assert on it."""

    claims: int
    evidence: int
    contradictions: int
    clusters: int
    #: Sources the projection judged to be copies of another source.
    duplicates: int


class EvidenceProjector:
    """Writes a run's claims, spans and contradictions, and clusters its sources.

    Takes a store rather than a database: the unit of work is a whole run's
    results, and the report projected beside it cites the claims written here, so
    the two share one session and one transaction (``app.research.recorder``). A
    reader never sees a citation whose claim has not landed.
    """

    def __init__(self, store: EvidenceStore) -> None:
        self._store = store

    async def record(self, state: ResearchState, *, now: dt.datetime) -> Projected:
        """Project one run's state. Safe to call again with the same state."""
        research_id = state["research_id"]
        claims = tuple(state.get("claims") or ())
        evidence = {item.id: item for item in state.get("evidence") or ()}
        contradictions = tuple(state.get("contradictions") or ())

        store = self._store
        clusters = cluster_sources(research_id, await store.fingerprints(research_id))
        await store.assign_clusters(clusters)

        cluster_of = {
            source_id: cluster.cluster_id
            for cluster in clusters
            for source_id in cluster.source_ids
        }
        claim_rows = [
            _claim_record(claim, research_id, evidence, cluster_of, now=now) for claim in claims
        ]
        evidence_rows = _evidence_records(claims, evidence)
        contradiction_rows = _contradiction_records(
            contradictions, claims, evidence, research_id, now=now
        )
        await store.record_claims(claim_rows)
        await store.record_evidence(evidence_rows)
        await store.record_contradictions(contradiction_rows)
        await store.refresh_counts(research_id)

        # Counted from the rows written, not from what the state held: both
        # builders drop what they cannot write, and a summary that reported the
        # larger number would be the one thing this module exists to prevent.
        projected = Projected(
            claims=len(claim_rows),
            evidence=len(evidence_rows),
            contradictions=len(contradiction_rows),
            clusters=len(clusters),
            duplicates=sum(len(cluster.duplicate_source_ids) for cluster in clusters),
        )
        logger.info(
            "evidence projected",
            extra={
                "research_id": str(research_id),
                "claims": projected.claims,
                "evidence": projected.evidence,
                "contradictions": projected.contradictions,
                "sources": len(cluster_of),
                "clusters": projected.clusters,
                "duplicate_sources": projected.duplicates,
            },
        )
        return projected


def _claim_record(
    claim: ClaimItem,
    research_id: uuid.UUID,
    evidence: dict[uuid.UUID, EvidenceItem],
    cluster_of: dict[uuid.UUID, uuid.UUID],
    *,
    now: dt.datetime,
) -> ClaimRecord:
    spans = [evidence[eid] for eid in claim.evidence_ids if eid in evidence]
    subject, predicate = _key_parts(claim.normalized_key)
    return ClaimRecord(
        id=claim.id,
        run_id=research_id,
        # The subtask that found the first span behind the claim. The relational
        # task row is written by the worker (Phase 13); until it exists the key
        # is what joins a claim to the question that produced it.
        task_external_id=spans[0].task_key if spans else None,
        text=claim.text,
        subject=subject,
        predicate=predicate,
        object_value=claim.object_value,
        claim_type=ClaimType(claim.claim_type),
        normalized_key=claim.normalized_key,
        confidence=claim.confidence,
        status=claim.status,
        corroboration_count=_corroboration(spans, cluster_of),
        # Not "now" on a re-projection: the row's own first_seen_at is kept by
        # the upsert, so this is the value only for a claim being written first.
        first_seen_at=now,
    )


def _corroboration(spans: Sequence[EvidenceItem], cluster_of: dict[uuid.UUID, uuid.UUID]) -> int:
    """Distinct clusters behind a claim.

    A source with no cluster is its own: clustering covers every source of the
    run, so this only happens for a span pointing at a source the run no longer
    has, and counting it as one is the same answer clustering would have given.
    """
    return len({cluster_of.get(span.source_id, span.source_id) for span in spans})


def _evidence_records(
    claims: Sequence[ClaimItem], evidence: dict[uuid.UUID, EvidenceItem]
) -> list[EvidenceRecord]:
    """One row per (claim, span). A span cited by nothing is not evidence.

    Extraction can produce more spans than normalization turns into claims - a
    round whose claim budget ran out, a span the normalizer judged irrelevant -
    and those are working material, not results. The table's foreign key says as
    much: evidence belongs to a claim.
    """
    rows: list[EvidenceRecord] = []
    for claim in claims:
        for evidence_id in claim.evidence_ids:
            span = evidence.get(evidence_id)
            if span is None:
                # The normalizer only cites spans it was shown, so this means a
                # checkpoint whose evidence list was trimmed. Dropping the row is
                # right: an evidence row with no span has nothing to quote.
                continue
            rows.append(
                EvidenceRecord(
                    id=evidence_link_id(claim.id, span.id),
                    claim_id=claim.id,
                    document_id=span.document_id,
                    source_id=span.source_id,
                    span_text=span.claim_text,
                    span_start=span.span_start,
                    span_end=span.span_end,
                    stance=span.stance,
                    extractor_agent=AgentName.EVIDENCE_EXTRACTOR.value,
                    extractor_model=span.extractor_model or UNKNOWN_MODEL,
                    # How firmly this span establishes this claim is what the
                    # normalizer scored and the verifier re-scored. The extractor
                    # scores no span on its own, and a per-span number invented
                    # here would be a measurement nobody made.
                    confidence=claim.confidence,
                )
            )
    return rows


def _contradiction_records(
    contradictions: Sequence[ContradictionItem],
    claims: Sequence[ClaimItem],
    evidence: dict[uuid.UUID, EvidenceItem],
    research_id: uuid.UUID,
    *,
    now: dt.datetime,
) -> list[ContradictionRecord]:
    """Disagreements, with the values and sources that hold them.

    A contradiction whose claims are no longer in state is dropped rather than
    written with a dangling foreign key. It cannot be displayed without them.
    """
    by_id = {claim.id: claim for claim in claims}
    rows: list[ContradictionRecord] = []
    for item in contradictions:
        first, second = by_id.get(item.claim_a_id), by_id.get(item.claim_b_id)
        if first is None or second is None:
            continue
        source_a = _first_source(first, evidence)
        source_b = _first_source(second, evidence)
        if source_a is None or source_b is None:
            continue
        rows.append(
            ContradictionRecord(
                id=item.id,
                run_id=research_id,
                normalized_key=item.normalized_key,
                claim_a_id=first.id,
                claim_b_id=second.id,
                # The asserted value where the normalizer could quote one from
                # the claim, and the claim itself where it could not. Never a
                # value composed here: a reader takes this for what the source
                # said.
                value_a=first.object_value or first.text,
                value_b=second.object_value or second.text,
                source_a_id=source_a,
                source_b_id=source_b,
                likely_reason=item.likely_reason,
                resolution=ContradictionResolution.UNRESOLVED,
                detected_at=now,
            )
        )
    return rows


def _first_source(claim: ClaimItem, evidence: dict[uuid.UUID, EvidenceItem]) -> uuid.UUID | None:
    """The source behind a claim's first span, in the order it cited them."""
    for evidence_id in claim.evidence_ids:
        span = evidence.get(evidence_id)
        if span is not None:
            return span.source_id
    return None


def _key_parts(normalized_key: str) -> tuple[str, str]:
    """The subject and predicate a normalized key already contains.

    The normalizer writes ``subject | predicate | qualifier``; these are the
    triple the schema stores (TDD 7.2) and the knowledge-graph projection later
    reads. A key that does not parse into parts yields what it has and empty
    strings for the rest, which is what the columns' server defaults mean.
    """
    parts = [part.strip() for part in normalized_key.split("|")]
    subject = parts[0] if parts else ""
    predicate = parts[1] if len(parts) > 1 else ""
    return subject[:2000], predicate[:2000]
