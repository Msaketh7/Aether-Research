"""Postgres storage for the evidence chain.

Implements ``EvidenceStore``: the writes the projection makes after a run, and
the reads behind ``GET /research/{id}/sources`` and ``/evidence``.

Every write is an upsert on a derived primary key, because the projection runs
again whenever a run resumes (see ``app.evidence.projection``). Two of them keep
a column the row already has rather than overwriting it, and each is a decision
rather than an oversight:

* a claim keeps its ``first_seen_at`` - it is when the run first believed this,
  and a re-projection is not a new belief;
* a contradiction keeps its ``resolution`` and ``resolved_by`` - the projection
  only ever writes ``unresolved``, so overwriting would undo a person's
  judgement, and FR-7 cuts both ways: nothing is silently resolved, and nothing
  silently un-resolves either.

Every read joins ``research_runs`` for ``user_id``. These tables carry no owner
of their own - a claim belongs to a run, and the run belongs to a person - so
the join is the authorisation, and it is in the WHERE clause rather than applied
to the rows afterwards.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import Select, Table, and_, bindparam, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import (
    ClaimStatus,
    ClaimType,
    ContradictionResolution,
    EvidenceStance,
    SourceType,
)
from app.core.logging import get_logger
from app.core.pagination import encode_cursor
from app.db.models.evidence import ClaimRow, ContradictionRow, EvidenceRow
from app.db.models.research import ResearchRunRow
from app.db.models.source import SourceRow
from app.evidence.dedup import Cluster, ClusterReason, SourceFingerprint, cluster_identity
from app.evidence.repository import ClaimRecord, ContradictionRecord, EvidenceRecord
from app.evidence.schemas import Claim, ClaimWithEvidence, Contradiction, Evidence, EvidenceResponse
from app.sources.credibility import assess
from app.sources.schemas import Source, SourceCluster, SourceCredibility, SourcesResponse

logger = get_logger(__name__)

#: The three tables this writes to, as tables rather than mapped classes. The
#: writes are bulk upserts on a derived primary key - ``INSERT ... ON CONFLICT``
#: over a list of rows - which is a Core statement, not something the ORM's unit
#: of work expresses. ``__table__`` is typed as a ``FromClause``; it is a
#: ``Table``, and naming it once here keeps the narrowing in one place.
_CLAIMS = cast(Table, ClaimRow.__table__)
_EVIDENCE = cast(Table, EvidenceRow.__table__)
_CONTRADICTIONS = cast(Table, ContradictionRow.__table__)
_SOURCES = cast(Table, SourceRow.__table__)

#: Guard on every list query here, as in the runs repository.
ABSOLUTE_MAX_ROWS = 200

#: The clustering rules, as the column stores them.
_CLUSTER_REASONS = frozenset({"exact_hash", "canonical_url", "near_duplicate"})

#: Sources one clustering pass will read. A run's sources are capped by the FR-8
#: ceiling long before this; the bound exists so that a bug elsewhere becomes a
#: truncated cluster pass with a log line rather than a worker holding a whole
#: table in memory.
MAX_CLUSTERED_SOURCES = 1000

#: Contradictions returned with a page of claims. They are a run's whole conflict
#: list rather than a page of it, so the ceiling is the honest place to say that
#: a run with more than this has more than is shown.
MAX_CONTRADICTIONS = 200

#: Spans loaded per claim on a page. Mirrors the graph's ceiling on how many
#: spans one claim may cite (``MAX_EVIDENCE_PER_CLAIM``), which is what makes the
#: per-page bound below exact rather than a guess: a claim cannot hold more, so
#: nothing is ever truncated in practice, and a page's cost stays proportional to
#: the page rather than to the run.
MAX_EVIDENCE_PER_CLAIM = 50


def _to_float(value: Decimal | float | None) -> float:
    return float(value) if value is not None else 0.0


def _optional_float(value: Decimal | float | None) -> float | None:
    """Numeric that may legitimately be NULL. ``None`` means *not measured*."""
    return float(value) if value is not None else None


class SqlAlchemyEvidenceRepository:
    """Reads and writes claims, evidence, contradictions and source clusters."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- writing ----------------------------------------------------------

    async def fingerprints(self, run_id: uuid.UUID) -> list[SourceFingerprint]:
        statement = (
            select(
                SourceRow.id,
                SourceRow.canonical_url,
                SourceRow.content_hash,
                SourceRow.title,
                SourceRow.excerpt,
                SourceRow.accessed_at,
            )
            .where(SourceRow.run_id == run_id)
            .order_by(SourceRow.accessed_at, SourceRow.id)
            .limit(MAX_CLUSTERED_SOURCES + 1)
        )
        rows = list((await self._session.execute(statement)).all())
        if len(rows) > MAX_CLUSTERED_SOURCES:
            logger.warning(
                "run has more sources than one clustering pass reads",
                extra={"research_id": str(run_id), "limit": MAX_CLUSTERED_SOURCES},
            )
            rows = rows[:MAX_CLUSTERED_SOURCES]
        return [
            SourceFingerprint(
                source_id=row.id,
                canonical_url=row.canonical_url,
                content_hash=row.content_hash,
                title=row.title,
                text=row.excerpt,
                accessed_at=row.accessed_at,
            )
            for row in rows
        ]

    async def assign_clusters(self, clusters: Sequence[Cluster]) -> None:
        values = [
            {
                "b_id": source_id,
                "dedup_cluster_id": cluster.cluster_id,
                "dedup_reason": cluster.reason,
            }
            for cluster in clusters
            for source_id in cluster.source_ids
        ]
        if not values:
            return
        await self._session.execute(
            update(_SOURCES).where(_SOURCES.c.id == bindparam("b_id")), values
        )

    async def record_claims(self, claims: Sequence[ClaimRecord]) -> None:
        if not claims:
            return
        table = _CLAIMS
        statement = insert(table).values(
            [
                {
                    "id": claim.id,
                    "run_id": claim.run_id,
                    "task_external_id": claim.task_external_id,
                    "text": claim.text,
                    "subject": claim.subject,
                    "predicate": claim.predicate,
                    "object_value": claim.object_value,
                    "claim_type": claim.claim_type.value,
                    "normalized_key": claim.normalized_key,
                    "confidence": claim.confidence,
                    "status": claim.status.value,
                    "corroboration_count": claim.corroboration_count,
                    "first_seen_at": claim.first_seen_at,
                }
                for claim in claims
            ]
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[table.c.id],
                # Everything the run can revise, and nothing else. A later round
                # re-scores a claim, finds more corroboration for it and may
                # reword it; when it first believed it does not change. The
                # table carries no `updated_at` - these rows are a projection,
                # and when they were last rewritten says nothing about the run.
                set_={
                    "text": statement.excluded.text,
                    "subject": statement.excluded.subject,
                    "predicate": statement.excluded.predicate,
                    "object_value": statement.excluded.object_value,
                    "claim_type": statement.excluded.claim_type,
                    "confidence": statement.excluded.confidence,
                    "status": statement.excluded.status,
                    "corroboration_count": statement.excluded.corroboration_count,
                    "task_external_id": statement.excluded.task_external_id,
                },
            )
        )

    async def record_evidence(self, evidence: Sequence[EvidenceRecord]) -> None:
        if not evidence:
            return
        table = _EVIDENCE
        statement = insert(table).values(
            [
                {
                    "id": item.id,
                    "claim_id": item.claim_id,
                    "document_id": item.document_id,
                    "source_id": item.source_id,
                    "span_text": item.span_text,
                    "span_start": item.span_start,
                    "span_end": item.span_end,
                    "stance": item.stance.value,
                    "extractor_agent": item.extractor_agent,
                    "extractor_model": item.extractor_model,
                    "confidence": item.confidence,
                }
                for item in evidence
            ]
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[table.c.id],
                # The span and its offsets are keyed by their own content, so a
                # second projection re-states them identically. Confidence can
                # move: it is the claim's, and the verifier re-scores that.
                set_={
                    "stance": statement.excluded.stance,
                    "confidence": statement.excluded.confidence,
                    "extractor_model": statement.excluded.extractor_model,
                },
            )
        )

    async def record_contradictions(self, rows: Sequence[ContradictionRecord]) -> None:
        if not rows:
            return
        table = _CONTRADICTIONS
        statement = insert(table).values(
            [
                {
                    "id": row.id,
                    "run_id": row.run_id,
                    "normalized_key": row.normalized_key,
                    "claim_a_id": row.claim_a_id,
                    "claim_b_id": row.claim_b_id,
                    "value_a": row.value_a,
                    "value_b": row.value_b,
                    "source_a_id": row.source_a_id,
                    "source_b_id": row.source_b_id,
                    "likely_reason": row.likely_reason,
                    "resolution": row.resolution.value,
                    "detected_at": row.detected_at,
                }
                for row in rows
            ]
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[table.c.id],
                # `resolution`, `resolved_by` and `detected_at` are absent on
                # purpose. The projection writes only `unresolved`, so updating
                # them would overwrite a person's judgement with the system's
                # silence - which is the same failure FR-7 forbids, inverted.
                set_={
                    "value_a": statement.excluded.value_a,
                    "value_b": statement.excluded.value_b,
                    "likely_reason": statement.excluded.likely_reason,
                },
            )
        )

    async def refresh_counts(self, run_id: uuid.UUID) -> None:
        """Recompute every denormalised count from the rows beneath it."""
        claims_per_source = (
            select(
                EvidenceRow.source_id.label("source_id"),
                func.count(func.distinct(EvidenceRow.claim_id)).label("claims"),
            )
            .join(ClaimRow, ClaimRow.id == EvidenceRow.claim_id)
            .where(ClaimRow.run_id == run_id)
            .group_by(EvidenceRow.source_id)
            .subquery()
        )
        await self._session.execute(
            update(SourceRow)
            .where(SourceRow.run_id == run_id)
            .values(
                # Coalesced outside the subquery, which returns no row at all for
                # a source nothing cites. A source that was read and produced no
                # claim is 0 - a measurement - and the column is NOT NULL.
                claim_count=func.coalesce(
                    select(claims_per_source.c.claims)
                    .where(claims_per_source.c.source_id == SourceRow.id)
                    .scalar_subquery(),
                    0,
                )
            )
        )
        await self._session.execute(
            update(ResearchRunRow)
            .where(ResearchRunRow.id == run_id)
            .values(
                claim_count=select(func.count())
                .select_from(ClaimRow)
                .where(ClaimRow.run_id == run_id)
                .scalar_subquery(),
                contradiction_count=select(func.count())
                .select_from(ContradictionRow)
                .where(ContradictionRow.run_id == run_id)
                .scalar_subquery(),
            )
        )

    async def commit(self) -> None:
        await self._session.commit()

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
        bounded = max(1, min(limit, ABSOLUTE_MAX_ROWS))
        base = self._owned_sources(run_id, user_id)
        if source_type is not None:
            base = base.where(SourceRow.source_type == source_type.value)

        total = await self._count(base)
        statement = base
        if after_id is not None:
            anchor = (
                await self._session.execute(
                    select(SourceRow.created_at, SourceRow.id).where(
                        SourceRow.id == after_id, SourceRow.run_id == run_id
                    )
                )
            ).one_or_none()
            if anchor is None:
                # As in the runs repository: a cursor pointing at a row that is
                # gone ends the traversal instead of restarting it.
                return SourcesResponse(sources=[], clusters=[], next_cursor=None, total=total)
            created_at, anchor_id = anchor
            statement = statement.where(
                or_(
                    SourceRow.created_at > created_at,
                    and_(SourceRow.created_at == created_at, SourceRow.id > anchor_id),
                )
            )

        statement = statement.order_by(SourceRow.created_at, SourceRow.id).limit(bounded + 1)
        rows = list((await self._session.execute(statement)).scalars())
        page = rows[:bounded]
        has_more = len(rows) > bounded
        return SourcesResponse(
            sources=[_to_source(row) for row in page],
            clusters=await self._clusters_for(run_id, page),
            next_cursor=encode_cursor(str(page[-1].id)) if has_more and page else None,
            total=total,
        )

    async def evidence_page(
        self,
        run_id: uuid.UUID,
        *,
        user_id: uuid.UUID,
        limit: int,
        after_id: uuid.UUID | None = None,
        status: ClaimStatus | None = None,
    ) -> EvidenceResponse:
        bounded = max(1, min(limit, ABSOLUTE_MAX_ROWS))
        base = (
            select(ClaimRow)
            .join(ResearchRunRow, ResearchRunRow.id == ClaimRow.run_id)
            .where(ClaimRow.run_id == run_id, ResearchRunRow.user_id == user_id)
        )
        if status is not None:
            base = base.where(ClaimRow.status == status.value)

        total = await self._count(base)
        statement = base
        if after_id is not None:
            anchor = (
                await self._session.execute(
                    select(ClaimRow.created_at, ClaimRow.id).where(
                        ClaimRow.id == after_id, ClaimRow.run_id == run_id
                    )
                )
            ).one_or_none()
            if anchor is None:
                return EvidenceResponse(
                    claims=[],
                    contradictions=await self._contradictions(run_id, user_id),
                    next_cursor=None,
                    total=total,
                )
            created_at, anchor_id = anchor
            statement = statement.where(
                or_(
                    ClaimRow.created_at > created_at,
                    and_(ClaimRow.created_at == created_at, ClaimRow.id > anchor_id),
                )
            )

        statement = statement.order_by(ClaimRow.created_at, ClaimRow.id).limit(bounded + 1)
        rows = list((await self._session.execute(statement)).scalars())
        page = rows[:bounded]
        has_more = len(rows) > bounded
        spans = await self._evidence_for([row.id for row in page])
        return EvidenceResponse(
            claims=[_to_claim(row, spans.get(row.id, [])) for row in page],
            contradictions=await self._contradictions(run_id, user_id),
            next_cursor=encode_cursor(str(page[-1].id)) if has_more and page else None,
            total=total,
        )

    # --- read helpers -----------------------------------------------------

    def _owned_sources(self, run_id: uuid.UUID, user_id: uuid.UUID) -> Select[tuple[SourceRow]]:
        return (
            select(SourceRow)
            .join(ResearchRunRow, ResearchRunRow.id == SourceRow.run_id)
            .where(SourceRow.run_id == run_id, ResearchRunRow.user_id == user_id)
        )

    async def _count(self, base: Select[Any]) -> int:
        statement = select(func.count()).select_from(base.order_by(None).subquery())
        return int((await self._session.execute(statement)).scalar_one())

    async def _clusters_for(
        self, run_id: uuid.UUID, page: Sequence[SourceRow]
    ) -> list[SourceCluster]:
        """The clusters the sources on this page belong to, with every member.

        A page can hold a duplicate whose primary is on another page, so the
        members are read by cluster id rather than assembled from the page.
        Clusters of one are omitted: they say nothing a reader needs, and every
        source that is not in this list is in a cluster of its own.
        """
        ids = {row.dedup_cluster_id for row in page if row.dedup_cluster_id is not None}
        if not ids:
            return []
        statement = (
            select(SourceRow.id, SourceRow.dedup_cluster_id, SourceRow.dedup_reason)
            .where(SourceRow.run_id == run_id, SourceRow.dedup_cluster_id.in_(tuple(ids)))
            .order_by(SourceRow.accessed_at, SourceRow.id)
            .limit(ABSOLUTE_MAX_ROWS * 2)
        )
        members: dict[uuid.UUID, list[uuid.UUID]] = {}
        reasons: dict[uuid.UUID, str] = {}
        for row in (await self._session.execute(statement)).all():
            members.setdefault(row.dedup_cluster_id, []).append(row.id)
            if row.dedup_reason:
                reasons[row.dedup_cluster_id] = row.dedup_reason

        clusters: list[SourceCluster] = []
        for cluster_id, source_ids in members.items():
            if len(source_ids) < 2:
                continue
            # The primary is the member that reproduces the cluster id, since the
            # id is a UUID5 over the run and that source. Nothing stores it, and
            # nothing has to: the id *is* the statement of which one it is.
            primary = next(
                (sid for sid in source_ids if cluster_identity(run_id, sid) == cluster_id),
                source_ids[0],
            )
            clusters.append(
                SourceCluster(
                    cluster_id=cluster_id,
                    primary_source_id=primary,
                    duplicate_source_ids=[sid for sid in source_ids if sid != primary],
                    reason=_reason(reasons.get(cluster_id)),
                )
            )
        return clusters

    async def _evidence_for(
        self, claim_ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, list[EvidenceRow]]:
        if not claim_ids:
            return {}
        statement = (
            select(EvidenceRow)
            .where(EvidenceRow.claim_id.in_(tuple(claim_ids)))
            .order_by(EvidenceRow.claim_id, EvidenceRow.created_at, EvidenceRow.id)
            .limit(len(claim_ids) * MAX_EVIDENCE_PER_CLAIM)
        )
        grouped: dict[uuid.UUID, list[EvidenceRow]] = {}
        for row in (await self._session.execute(statement)).scalars():
            grouped.setdefault(row.claim_id, []).append(row)
        return grouped

    async def _contradictions(self, run_id: uuid.UUID, user_id: uuid.UUID) -> list[Contradiction]:
        """This run's disagreements, with the text of the claims they name."""
        claim_a = _CLAIMS.alias("claim_a")
        claim_b = _CLAIMS.alias("claim_b")
        statement = (
            select(ContradictionRow, claim_a.c.text.label("text_a"), claim_b.c.text.label("text_b"))
            .join(ResearchRunRow, ResearchRunRow.id == ContradictionRow.run_id)
            .join(claim_a, claim_a.c.id == ContradictionRow.claim_a_id)
            .join(claim_b, claim_b.c.id == ContradictionRow.claim_b_id)
            .where(ContradictionRow.run_id == run_id, ResearchRunRow.user_id == user_id)
            .order_by(ContradictionRow.detected_at, ContradictionRow.id)
            .limit(MAX_CONTRADICTIONS)
        )
        return [
            Contradiction(
                id=row.ContradictionRow.id,
                run_id=row.ContradictionRow.run_id,
                normalized_key=row.ContradictionRow.normalized_key,
                claim_a_id=row.ContradictionRow.claim_a_id,
                claim_b_id=row.ContradictionRow.claim_b_id,
                claim_a_text=row.text_a,
                claim_b_text=row.text_b,
                value_a=row.ContradictionRow.value_a,
                value_b=row.ContradictionRow.value_b,
                source_a_id=row.ContradictionRow.source_a_id,
                source_b_id=row.ContradictionRow.source_b_id,
                likely_reason=row.ContradictionRow.likely_reason,
                resolution=ContradictionResolution(row.ContradictionRow.resolution),
                resolved_by=row.ContradictionRow.resolved_by,
                detected_at=row.ContradictionRow.detected_at,
            )
            for row in (await self._session.execute(statement)).all()
        ]


# --- row to DTO -----------------------------------------------------------


def _reason(stored: str | None) -> ClusterReason:
    """The stored rule, or the certain one for a row written before it existed."""
    return stored if stored in _CLUSTER_REASONS else "exact_hash"  # type: ignore[return-value]


def _to_source(row: SourceRow) -> Source:
    return Source(
        id=row.id,
        run_id=row.run_id,
        url=row.url,
        canonical_url=row.canonical_url,
        domain=row.domain,
        source_type=SourceType(row.source_type),
        title=row.title,
        publisher=row.publisher,
        author=row.author,
        published_at=row.published_at,
        accessed_at=row.accessed_at,
        content_hash=row.content_hash,
        credibility_score=_to_float(row.credibility_score),
        credibility_metadata=_credibility(row),
        dedup_cluster_id=row.dedup_cluster_id,
        relevance_score=_optional_float(row.relevance_score),
        task_external_id=row.task_external_id,
        claim_count=row.claim_count,
        excerpt=row.excerpt,
    )


def _credibility(row: SourceRow) -> SourceCredibility:
    """The stored assessment, or the same function recomputed from the row.

    Ingestion writes this column from ``app.sources.credibility``, so the stored
    value is normally exactly what re-running it would produce. The fallback is
    for a row written before that was true: recomputing is not a guess, it is the
    same declared table applied to the same inputs - whereas raising would take
    the whole sources page down over one legacy row.
    """
    try:
        return SourceCredibility.model_validate(row.credibility_metadata)
    except ValidationError:
        logger.info(
            "source credibility recomputed from its origin",
            extra={"source_id": str(row.id), "domain": row.domain},
        )
        return SourceCredibility.model_validate(
            assess(SourceType(row.source_type), row.domain).as_metadata()
        )


def _to_claim(row: ClaimRow, spans: Sequence[EvidenceRow]) -> ClaimWithEvidence:
    claim = Claim(
        id=row.id,
        run_id=row.run_id,
        task_id=row.task_id,
        task_external_id=row.task_external_id,
        text=row.text_,
        subject=row.subject,
        predicate=row.predicate,
        object_value=row.object_value,
        claim_type=ClaimType(row.claim_type),
        normalized_key=row.normalized_key,
        confidence=_to_float(row.confidence),
        status=ClaimStatus(row.status),
        corroboration_count=row.corroboration_count,
        first_seen_at=row.first_seen_at,
    )
    evidence = [_to_evidence(span) for span in spans]
    return ClaimWithEvidence(
        **claim.model_dump(),
        # Two lists, and every span in them carries its own stance label, so a
        # neutral span - relevant context that settles nothing - sits with the
        # supporting ones and is still shown as neutral. Dropping it would hide
        # part of why a claim is believed; the one thing that must not happen is
        # a refuting span reaching the reader as support.
        supporting=[item for item in evidence if item.stance is not EvidenceStance.REFUTES],
        refuting=[item for item in evidence if item.stance is EvidenceStance.REFUTES],
    )


def _to_evidence(row: EvidenceRow) -> Evidence:
    return Evidence(
        id=row.id,
        claim_id=row.claim_id,
        source_id=row.source_id,
        document_id=row.document_id,
        span_text=row.span_text,
        span_start=row.span_start,
        span_end=row.span_end,
        stance=EvidenceStance(row.stance),
        extractor_agent=row.extractor_agent,
        extractor_model=row.extractor_model,
        confidence=_to_float(row.confidence),
        created_at=row.created_at,
    )
