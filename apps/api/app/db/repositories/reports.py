"""Postgres storage for reports, sections and citations.

Implements ``ReportStore``: what the projection writes after a run, and the read
behind ``GET /research/{id}/report``.

The write is a replace rather than a merge. A repaired draft can drop a section
and cite a different claim, so the previous revision's sections and citations
must go - upserting alone would leave them numbered into the middle of the new
report. Sections and citations are deleted for this report and rewritten in one
statement each, inside the caller's transaction, so no reader ever sees half a
report.

The read joins ``research_runs`` for ``user_id``, like every other read in this
package: a report belongs to a run, and the run belongs to a person.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import Table, delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ReportSectionKind, ReportStatus
from app.core.logging import get_logger
from app.db.models.report import CitationRow, ReportRow, ReportSectionRow
from app.db.models.research import ResearchRunRow
from app.db.models.source import SourceRow
from app.reports.repository import (
    CitationRecord,
    CitedSource,
    ReportRecord,
    SectionRecord,
    ValidationRecord,
)
from app.reports.schemas import (
    Citation,
    CitationRejection,
    CitationValidationSummary,
    Report,
    ReportResponse,
    ReportSection,
)

logger = get_logger(__name__)

_REPORTS = cast(Table, ReportRow.__table__)
_SECTIONS = cast(Table, ReportSectionRow.__table__)
_CITATIONS = cast(Table, CitationRow.__table__)

#: Sources read for one run's reference list. A run's sources are capped by the
#: FR-8 ceiling well below this; the bound is here so a bug upstream truncates a
#: list rather than loading a table.
MAX_SOURCES = 1000

#: Citations returned with one report. A report cites tens; this is the ceiling
#: at which the response stops growing, and it is far above any real report.
MAX_CITATIONS = 2000


def _to_float(value: Decimal | float | None) -> float:
    return float(value) if value is not None else 0.0


def _optional_float(value: Decimal | float | None) -> float | None:
    """Numeric that may legitimately be NULL: ``None`` means *not computed*."""
    return float(value) if value is not None else None


class SqlAlchemyReportRepository:
    """Reads and writes one run's report."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- writing ----------------------------------------------------------

    async def cited_sources(self, run_id: uuid.UUID) -> dict[uuid.UUID, CitedSource]:
        statement = (
            select(
                SourceRow.id,
                SourceRow.title,
                SourceRow.url,
                SourceRow.publisher,
                SourceRow.accessed_at,
            )
            .where(SourceRow.run_id == run_id)
            .order_by(SourceRow.accessed_at, SourceRow.id)
            .limit(MAX_SOURCES)
        )
        return {
            row.id: CitedSource(
                id=row.id,
                title=row.title,
                url=row.url,
                publisher=row.publisher,
                accessed_at=row.accessed_at,
            )
            for row in (await self._session.execute(statement)).all()
        }

    async def record_report(
        self,
        report: ReportRecord,
        sections: Sequence[SectionRecord],
        citations: Sequence[CitationRecord],
    ) -> None:
        statement = insert(_REPORTS).values(
            {
                "id": report.id,
                "run_id": report.run_id,
                "title": report.title,
                "summary": report.summary,
                "overall_confidence": report.overall_confidence,
                "status": report.status.value,
                "model": report.model,
                "word_count": report.word_count,
                "generated_at": report.generated_at,
                "validated_at": report.validated_at,
                "coverage_caveat": report.coverage_caveat,
                "validation": _validation_json(report.validation),
            }
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[_REPORTS.c.id],
                # Everything a rewrite can change. `run_id` is what the id is
                # derived from, so it cannot.
                set_={
                    "title": statement.excluded.title,
                    "summary": statement.excluded.summary,
                    "overall_confidence": statement.excluded.overall_confidence,
                    "status": statement.excluded.status,
                    "model": statement.excluded.model,
                    "word_count": statement.excluded.word_count,
                    "generated_at": statement.excluded.generated_at,
                    "validated_at": statement.excluded.validated_at,
                    "coverage_caveat": statement.excluded.coverage_caveat,
                    "validation": statement.excluded.validation,
                },
            )
        )

        # Citations first: they cascade from sections, and deleting a section
        # while its citations stand would fail on the foreign key. Deleting them
        # wholesale rather than diffing is correct because they are derived -
        # every one is rebuilt from the same assembly, with the same id.
        await self._session.execute(
            delete(_CITATIONS).where(
                _CITATIONS.c.report_section_id.in_(
                    select(_SECTIONS.c.id).where(_SECTIONS.c.report_id == report.id)
                )
            )
        )
        keep = [section.id for section in sections]
        await self._session.execute(
            delete(_SECTIONS).where(
                _SECTIONS.c.report_id == report.id,
                _SECTIONS.c.id.notin_(keep) if keep else _SECTIONS.c.id.isnot(None),
            )
        )

        if sections:
            section_insert = insert(_SECTIONS).values(
                [
                    {
                        "id": section.id,
                        "report_id": section.report_id,
                        "kind": section.kind.value,
                        "heading": section.heading,
                        "ordinal": section.ordinal,
                        "content_md": section.content_md,
                    }
                    for section in sections
                ]
            )
            await self._session.execute(
                section_insert.on_conflict_do_update(
                    index_elements=[_SECTIONS.c.id],
                    set_={
                        "heading": section_insert.excluded.heading,
                        "ordinal": section_insert.excluded.ordinal,
                        "content_md": section_insert.excluded.content_md,
                    },
                )
            )
        if citations:
            await self._session.execute(
                insert(_CITATIONS).values(
                    [
                        {
                            "id": citation.id,
                            "report_section_id": citation.report_section_id,
                            "claim_id": citation.claim_id,
                            "source_id": citation.source_id,
                            "ordinal": citation.ordinal,
                            "quote": citation.quote,
                            "confidence": citation.confidence,
                        }
                        for citation in citations
                    ]
                )
            )
        logger.info(
            "report projected",
            extra={
                "research_id": str(report.run_id),
                "status": report.status.value,
                "sections": len(sections),
                "citations": len(citations),
                "word_count": report.word_count,
            },
        )

    # --- reading ----------------------------------------------------------

    async def report_for(self, run_id: uuid.UUID, *, user_id: uuid.UUID) -> ReportResponse | None:
        statement = (
            select(ReportRow)
            .join(ResearchRunRow, ResearchRunRow.id == ReportRow.run_id)
            .where(ReportRow.run_id == run_id, ResearchRunRow.user_id == user_id)
        )
        row = (await self._session.execute(statement)).scalar_one_or_none()
        if row is None:
            return None

        sections = list(
            (
                await self._session.execute(
                    select(ReportSectionRow)
                    .where(ReportSectionRow.report_id == row.id)
                    .order_by(ReportSectionRow.ordinal)
                )
            ).scalars()
        )
        citations = await self._citations([section.id for section in sections])
        return ReportResponse(
            report=_to_report(row),
            sections=[_to_section(section) for section in sections],
            citations=citations,
            validation=_to_validation(row),
        )

    async def _citations(self, section_ids: Sequence[uuid.UUID]) -> list[Citation]:
        if not section_ids:
            return []
        statement = (
            select(CitationRow, SourceRow)
            .join(SourceRow, SourceRow.id == CitationRow.source_id)
            .where(CitationRow.report_section_id.in_(tuple(section_ids)))
            .order_by(CitationRow.ordinal, CitationRow.id)
            .limit(MAX_CITATIONS)
        )
        return [
            Citation(
                id=row.CitationRow.id,
                ordinal=row.CitationRow.ordinal,
                report_section_id=row.CitationRow.report_section_id,
                claim_id=row.CitationRow.claim_id,
                source_id=row.CitationRow.source_id,
                source_title=row.SourceRow.title,
                source_url=row.SourceRow.url,
                source_publisher=row.SourceRow.publisher,
                quote=row.CitationRow.quote,
                confidence=_to_float(row.CitationRow.confidence),
            )
            for row in (await self._session.execute(statement)).all()
        ]


def _validation_json(record: ValidationRecord | None) -> dict[str, Any] | None:
    return (
        None
        if record is None
        else {
            "checked": record.checked,
            "valid": record.valid,
            "rejected": record.rejected,
            "reasons": [{"reason": reason, "count": count} for reason, count in record.reasons],
        }
    )


#: The validator's reason codes as a reader should see them. The code is what is
#: stored - stable, greppable, and what the logs and the repair prompt use - and
#: this is what the summary above the report says, because "no_such_claim" is a
#: sentence about the system rather than about the report.
_READABLE_REASONS = {
    "no_such_claim": "the citation named a claim this run does not have",
    "claim_not_in_run": "the claim is not part of this run",
    "claim_without_evidence": "the claim had no evidence behind it",
    "evidence_without_retrieved_source": (
        "the evidence did not belong to a source this run retrieved"
    ),
}


def _to_validation(row: ReportRow) -> CitationValidationSummary | None:
    """The stored verdict, paired with when it was reached.

    ``validated_at`` lives in its own column because it is a fact about the
    report's lifecycle; the counts live in JSON because they are one value read
    as a whole. A row with counts but no timestamp is a bug rather than a state,
    so both are required here and the summary is omitted if either is missing.
    """
    stored = row.validation
    if not stored or row.validated_at is None:
        return None
    reasons = stored.get("reasons") or []
    return CitationValidationSummary(
        checked=int(stored["checked"]),
        valid=int(stored["valid"]),
        rejected=int(stored["rejected"]),
        rejection_reasons=[
            CitationRejection(
                reason=_READABLE_REASONS.get(str(item["reason"]), str(item["reason"])),
                count=int(item["count"]),
            )
            for item in reasons
        ],
        validated_at=row.validated_at,
    )


def _to_report(row: ReportRow) -> Report:
    return Report(
        id=row.id,
        run_id=row.run_id,
        title=row.title,
        summary=row.summary,
        overall_confidence=_optional_float(row.overall_confidence),
        status=ReportStatus(row.status),
        model=row.model,
        word_count=row.word_count,
        generated_at=row.generated_at,
        validated_at=row.validated_at,
        coverage_caveat=row.coverage_caveat,
    )


def _to_section(row: ReportSectionRow) -> ReportSection:
    return ReportSection(
        id=row.id,
        report_id=row.report_id,
        kind=ReportSectionKind(row.kind),
        heading=row.heading,
        ordinal=row.ordinal,
        content_md=row.content_md,
    )
