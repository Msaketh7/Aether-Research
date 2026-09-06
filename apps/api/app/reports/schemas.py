"""Report, section and citation DTOs (FR-9, FR-12)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.core.enums import ReportSectionKind, ReportStatus
from app.research.schemas import ApiModel


class Citation(ApiModel):
    """The link behind every `[n]` in a report.

    Emitted only after the validator has confirmed the whole chain
    citation -> claim -> evidence -> document -> source resolves. `quote` is the
    verbatim evidence span, so the reader sees the proof without another
    request.
    """

    id: UUID
    ordinal: int = Field(ge=1)
    report_section_id: UUID
    claim_id: UUID
    source_id: UUID
    source_title: str
    source_url: str
    source_publisher: str
    quote: str
    confidence: float = Field(ge=0.0, le=1.0)


class ReportSection(ApiModel):
    id: UUID
    report_id: UUID
    kind: ReportSectionKind
    heading: str
    ordinal: int
    content_md: str


class Report(ApiModel):
    id: UUID
    run_id: UUID
    title: str
    summary: str
    overall_confidence: float = Field(ge=0.0, le=1.0)
    status: ReportStatus
    model: str
    word_count: int
    generated_at: datetime
    validated_at: datetime | None
    coverage_caveat: str | None


class CitationRejection(ApiModel):
    reason: str
    count: int


class CitationValidationSummary(ApiModel):
    """Outcome of the validation pass, shown before the prose.

    How many citations were rejected as unverifiable is context the reader needs
    before deciding how far to trust the text.
    """

    checked: int
    valid: int
    rejected: int
    rejection_reasons: list[CitationRejection]
    validated_at: datetime


class ReportResponse(ApiModel):
    report: Report
    sections: list[ReportSection]
    citations: list[Citation]
    validation: CitationValidationSummary | None
