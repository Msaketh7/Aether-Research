"""Persistence boundary for the evidence chain.

The protocol lives here, next to the domain it serves; the Postgres
implementation lives in ``app/db/repositories/evidence.py``. Same arrangement as
``app.research.repository``, and for the same reason: the projection and the
service talk to an interface, so the thing that turns a finished run into rows
has no opinion about SQL.

Two rules hold across every method:

* **Writes are idempotent.** Every id crossing this boundary is derived - a
  claim's from the run and its normalized key, a span's from the claim and the
  span, a contradiction's from its ordered pair - so projecting a run twice
  writes the same rows twice rather than a second copy of everything. That is
  not a nicety: a run resumes from its last checkpoint after a crash, and the
  nodes that already ran are not re-run but the state they produced is projected
  again.
* **Reads are scoped by ``user_id`` and bounded.** Scoped in the WHERE clause,
  through ``research_runs``, because these rows have no owner of their own; a
  filter applied after fetching is the bug that paginated endpoints expose.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.core.enums import (
    ClaimStatus,
    ClaimType,
    ContradictionResolution,
    EvidenceStance,
    SourceType,
)
from app.evidence.dedup import Cluster, SourceFingerprint
from app.evidence.schemas import EvidenceResponse
from app.sources.schemas import SourcesResponse


@dataclass(frozen=True, slots=True)
class ClaimRecord:
    """One claim, as the ``claims`` table holds it."""

    id: uuid.UUID
    run_id: uuid.UUID
    task_external_id: str | None
    text: str
    subject: str
    predicate: str
    object_value: str
    claim_type: ClaimType
    normalized_key: str
    confidence: float
    status: ClaimStatus
    #: Distinct dedup clusters behind the claim, not distinct rows: four copies
    #: of one wire story corroborate a claim once (``app.evidence.dedup``).
    corroboration_count: int
    first_seen_at: dt.datetime


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    """One span, as evidence *for one claim*.

    The table's grain is the link, not the span: ``evidence.claim_id`` is a
    single foreign key, and one quoted passage can be why two different claims
    are believed. So a span cited by two claims is two rows, and the id is
    derived from both - which is also what keeps re-projecting the same state
    from inserting a second copy.
    """

    id: uuid.UUID
    claim_id: uuid.UUID
    document_id: uuid.UUID
    source_id: uuid.UUID
    span_text: str
    span_start: int
    span_end: int
    stance: EvidenceStance
    extractor_agent: str
    extractor_model: str
    confidence: float


@dataclass(frozen=True, slots=True)
class ContradictionRecord:
    """One recorded disagreement. ``resolution`` is never written as resolved by
    the projection - FR-7 is that conflicts are surfaced, not settled."""

    id: uuid.UUID
    run_id: uuid.UUID
    normalized_key: str
    claim_a_id: uuid.UUID
    claim_b_id: uuid.UUID
    value_a: str
    value_b: str
    source_a_id: uuid.UUID
    source_b_id: uuid.UUID
    likely_reason: str
    resolution: ContradictionResolution
    detected_at: dt.datetime


class EvidenceStore(Protocol):
    """What the projection writes and what the research service reads."""

    # --- writing ----------------------------------------------------------

    async def fingerprints(self, run_id: uuid.UUID) -> list[SourceFingerprint]:
        """Every source of this run, in the shape clustering needs.

        Unscoped by user on purpose: the caller is the projection, running for a
        run it was handed by the worker, and it has no request principal. The
        run id is the scope.
        """
        ...

    async def assign_clusters(self, clusters: Sequence[Cluster]) -> None:
        """Write each source's cluster and the rule that put it there."""
        ...

    async def record_claims(self, claims: Sequence[ClaimRecord]) -> None: ...

    async def record_evidence(self, evidence: Sequence[EvidenceRecord]) -> None: ...

    async def record_contradictions(self, rows: Sequence[ContradictionRecord]) -> None: ...

    async def refresh_counts(self, run_id: uuid.UUID) -> None:
        """Recompute the denormalised counts from the rows that justify them.

        ``sources.claim_count`` and the run's claim and contradiction counts are
        displayed beside the evidence they summarise, so they are recomputed from
        it rather than incremented - an increment that runs twice on a resumed
        run is a count nobody can reconcile with the page below it.
        """
        ...

    async def commit(self) -> None:
        """Make the projection durable. The caller owns the transaction."""
        ...

    # --- reading ----------------------------------------------------------

    async def sources_page(
        self,
        run_id: uuid.UUID,
        *,
        user_id: uuid.UUID,
        limit: int,
        after_id: uuid.UUID | None = None,
        source_type: SourceType | None = None,
    ) -> SourcesResponse:
        """One page of this run's sources, with the clusters they belong to."""
        ...

    async def evidence_page(
        self,
        run_id: uuid.UUID,
        *,
        user_id: uuid.UUID,
        limit: int,
        after_id: uuid.UUID | None = None,
        status: ClaimStatus | None = None,
    ) -> EvidenceResponse:
        """One page of claims with their spans, and the run's contradictions.

        The contradictions are not paginated with the claims: they are a short,
        bounded list that the page displays as a whole, and a reader who saw
        half of them would be worse informed than one who saw none.
        """
        ...
