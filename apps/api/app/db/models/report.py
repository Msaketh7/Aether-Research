"""Reports, sections and citations.

``citations`` is the table the whole product is judged on. Its foreign keys to
``claims`` and ``sources`` are what turn "never fabricate a citation" from a
hope about the model into a constraint the database enforces: a citation whose
claim or source does not exist cannot be inserted at all.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ReportSectionKind, ReportStatus
from app.db.base import Base, TimestampMixin, fk_uuid, uuid_pk
from app.db.models.research import enum_check

if TYPE_CHECKING:
    from app.db.models.evidence import ClaimRow
    from app.db.models.research import ResearchRunRow


class ReportRow(Base, TimestampMixin):
    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = uuid_pk()
    #: Unique: a run has at most one report. A second one would mean two
    #: answers to the same question with no way to say which is current.
    run_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    #: Mean confidence of the claims this report cites. Nullable because a
    #: report that cites none has no mean, and a placeholder there is read as a
    #: measurement the system made (0008).
    overall_confidence: Mapped[float | None] = mapped_column(Numeric(3, 2))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The synthesiser model, recorded so a quality change is attributable.
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    generated_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    #: Null until the citation validator has passed over it. The API refuses to
    #: serve an unvalidated report as final.
    validated_at: Mapped[dt.datetime | None]
    coverage_caveat: Mapped[str | None] = mapped_column(Text)
    #: The citation validator's verdict: how many markers were checked, how many
    #: resolved, and why the rest did not. Shown above the prose, because how
    #: much of a report could not be verified is what a reader needs before
    #: deciding how far to trust it. NULL means the check never ran (0008).
    validation: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    run: Mapped[ResearchRunRow] = relationship(back_populates="report")
    sections: Mapped[list[ReportSectionRow]] = relationship(
        back_populates="report",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ReportSectionRow.ordinal",
    )

    __table_args__ = (
        enum_check("status", ReportStatus, "reports_status"),
        CheckConstraint("overall_confidence BETWEEN 0 AND 1", name="ck_reports_confidence_range"),
    )


class ReportSectionRow(Base, TimestampMixin):
    __tablename__ = "report_sections"

    id: Mapped[uuid.UUID] = uuid_pk()
    report_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("reports.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    heading: Mapped[str] = mapped_column(Text, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Markdown containing inline `[n]` markers that resolve against citations.
    content_md: Mapped[str] = mapped_column(Text, nullable=False)

    report: Mapped[ReportRow] = relationship(back_populates="sections")
    citations: Mapped[list[CitationRow]] = relationship(
        back_populates="section", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        enum_check("kind", ReportSectionKind, "report_sections_kind"),
        Index("uq_report_sections_report_id_ordinal", "report_id", "ordinal", unique=True),
    )


class CitationRow(Base, TimestampMixin):
    """The link behind every `[n]`."""

    __tablename__ = "citations"

    id: Mapped[uuid.UUID] = uuid_pk()
    report_section_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("report_sections.id", ondelete="CASCADE"), nullable=False
    )
    claim_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("claims.id", ondelete="RESTRICT"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    #: The `[n]` number. Stable within one report.
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The supporting evidence span, denormalised so the popover needs no join.
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(
        Numeric(3, 2), nullable=False, server_default=text("0.50")
    )

    section: Mapped[ReportSectionRow] = relationship(back_populates="citations")
    claim: Mapped[ClaimRow] = relationship(back_populates="citations")

    __table_args__ = (
        CheckConstraint("ordinal >= 1", name="ck_citations_ordinal_positive"),
        Index("ix_citations_claim_id", "claim_id"),
        Index("ix_citations_source_id", "source_id"),
    )
