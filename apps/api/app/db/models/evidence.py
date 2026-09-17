"""Claims, evidence spans and contradictions.

This is the traceability chain the product exists to keep honest. A claim is a
normalised assertion; evidence is the verbatim span that supports or refutes it,
with character offsets into the stored document; a contradiction records that
two sources disagree, without picking a winner (FR-7).
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    ClaimStatus,
    ClaimType,
    ContradictionResolution,
    EvidenceStance,
)
from app.db.base import Base, TimestampMixin, fk_uuid, uuid_pk
from app.db.models.research import enum_check

if TYPE_CHECKING:
    from app.db.models.report import CitationRow
    from app.db.models.research import ResearchRunRow, ResearchTaskRow
    from app.db.models.source import SourceRow


class ClaimRow(Base, TimestampMixin):
    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = fk_uuid(
        ForeignKey("research_tasks.id", ondelete="SET NULL")
    )
    task_external_id: Mapped[str | None] = mapped_column(String(80))

    text_: Mapped[str] = mapped_column("text", Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    predicate: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    object_value: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    claim_type: Mapped[str] = mapped_column(String(20), nullable=False)

    #: Groups the same assertion across sources. Contradiction detection is a
    #: self-join on this column, which is why it is indexed rather than derived
    #: at query time.
    normalized_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(
        Numeric(3, 2), nullable=False, server_default=text("0.50")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    #: Independent sources backing it. 1 means uncorroborated, which the UI
    #: says out loud rather than rounding up to "verified".
    corroboration_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    first_seen_at: Mapped[dt.datetime] = mapped_column(nullable=False)

    run: Mapped[ResearchRunRow] = relationship(back_populates="claims")
    task: Mapped[ResearchTaskRow | None] = relationship(back_populates="claims")
    evidence: Mapped[list[EvidenceRow]] = relationship(
        back_populates="claim", cascade="all, delete-orphan", passive_deletes=True
    )
    citations: Mapped[list[CitationRow]] = relationship(back_populates="claim")

    __table_args__ = (
        enum_check("claim_type", ClaimType, "claims_claim_type"),
        enum_check("status", ClaimStatus, "claims_status"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_claims_confidence_range"),
        CheckConstraint("corroboration_count >= 0", name="ck_claims_corroboration_non_negative"),
        # The evidence page: this run's claims, optionally filtered by status.
        Index("ix_claims_run_id_status", "run_id", "status"),
        # Contradiction detection: same key, same run, different sources.
        Index("ix_claims_run_id_normalized_key", "run_id", "normalized_key"),
    )


class EvidenceRow(Base, TimestampMixin):
    """The verbatim span behind a claim."""

    __tablename__ = "evidence"

    id: Mapped[uuid.UUID] = uuid_pk()
    claim_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )

    span_text: Mapped[str] = mapped_column(Text, nullable=False)
    #: Character offsets into documents.normalized_content. The citation
    #: validator re-reads the span at these offsets; if it no longer matches,
    #: the citation is rejected.
    span_start: Mapped[int] = mapped_column(Integer, nullable=False)
    span_end: Mapped[int] = mapped_column(Integer, nullable=False)
    stance: Mapped[str] = mapped_column(String(10), nullable=False)

    extractor_agent: Mapped[str] = mapped_column(String(50), nullable=False)
    extractor_model: Mapped[str] = mapped_column(String(120), nullable=False)
    confidence: Mapped[float] = mapped_column(
        Numeric(3, 2), nullable=False, server_default=text("0.50")
    )

    claim: Mapped[ClaimRow] = relationship(back_populates="evidence")
    source: Mapped[SourceRow] = relationship(back_populates="evidence")

    __table_args__ = (
        enum_check("stance", EvidenceStance, "evidence_stance"),
        CheckConstraint("span_end > span_start", name="ck_evidence_span_order"),
        CheckConstraint("span_start >= 0", name="ck_evidence_span_start_non_negative"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_evidence_confidence_range"),
        # Rendering a claim card: its supporting and refuting spans together.
        Index("ix_evidence_claim_id_stance", "claim_id", "stance"),
    )


class ContradictionRow(Base, TimestampMixin):
    """A recorded disagreement. `likely_reason` is explicitly a hypothesis."""

    __tablename__ = "contradictions"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )
    normalized_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    claim_a_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    claim_b_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    value_a: Mapped[str] = mapped_column(Text, nullable=False)
    value_b: Mapped[str] = mapped_column(Text, nullable=False)
    source_a_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    source_b_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )

    likely_reason: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    resolution: Mapped[str] = mapped_column(String(30), nullable=False)
    resolved_by: Mapped[str | None] = mapped_column(String(50))
    detected_at: Mapped[dt.datetime] = mapped_column(nullable=False)

    run: Mapped[ResearchRunRow] = relationship(back_populates="contradictions")

    __table_args__ = (
        enum_check("resolution", ContradictionResolution, "contradictions_resolution"),
        CheckConstraint("claim_a_id <> claim_b_id", name="ck_contradictions_distinct_claims"),
        Index("ix_contradictions_run_id_resolution", "run_id", "resolution"),
    )
