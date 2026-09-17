"""Persistence boundary for reports, sections and citations.

The protocol lives here, next to the domain it serves; the Postgres
implementation lives in ``app/db/repositories/reports.py``. Same arrangement as
``app.evidence.repository``, and the same two rules: every write is idempotent on
a derived id, and every read is scoped by ``user_id`` through ``research_runs``.

One rule is particular to this table. ``citations`` has foreign keys to
``claims`` and ``sources`` with ``ON DELETE RESTRICT``, which is the database
saying what the product promises: a citation whose claim or source does not
exist cannot be inserted at all. So the claims a report cites must be written
before its citations - in the same transaction, which is why the report and the
evidence chain are projected together (``app.research.recorder``).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.core.enums import ReportSectionKind, ReportStatus
from app.reports.schemas import ReportResponse


@dataclass(frozen=True, slots=True)
class CitedSource:
    """What a reference entry needs to say about a source it cites.

    Read from the row rather than from graph state: ``SourceRef`` carries a title
    and a URL, and a reference also needs the publisher and the date the run
    fetched it. A citation without an accessed date is a citation to whatever
    that URL says today.
    """

    id: uuid.UUID
    title: str
    url: str
    publisher: str
    accessed_at: dt.datetime


@dataclass(frozen=True, slots=True)
class ValidationRecord:
    """What the citation check found, as the report row stores it."""

    checked: int
    valid: int
    rejected: int
    #: Reason code to count. Empty when nothing was rejected.
    reasons: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class ReportRecord:
    id: uuid.UUID
    run_id: uuid.UUID
    title: str
    #: Plain text, markers stripped: it is a cache for list views, where a
    #: citation marker would render as an unresolvable one.
    summary: str
    #: Mean confidence of the distinct claims the report cites. ``None`` when no
    #: citation resolved, because there is then nothing to average - not 0.50,
    #: which is the column's old default and reads as a measurement.
    overall_confidence: float | None
    status: ReportStatus
    model: str
    word_count: int
    generated_at: dt.datetime
    #: When the citation check ran, whatever its verdict. A report that shipped
    #: with rejections was still validated; it failed.
    validated_at: dt.datetime | None
    coverage_caveat: str | None
    #: The verdict itself, stored beside the report because it is read with it.
    validation: ValidationRecord | None


@dataclass(frozen=True, slots=True)
class SectionRecord:
    id: uuid.UUID
    report_id: uuid.UUID
    kind: ReportSectionKind
    heading: str
    ordinal: int
    content_md: str


@dataclass(frozen=True, slots=True)
class CitationRecord:
    """One resolved ``[n]``: this section cites that claim, from that source."""

    id: uuid.UUID
    report_section_id: uuid.UUID
    claim_id: uuid.UUID
    source_id: uuid.UUID
    ordinal: int
    #: The evidence span, denormalised so the reader's popover needs no join -
    #: and so the quote shown can never drift from the one that was validated.
    quote: str
    confidence: float


class ReportStore(Protocol):
    """What the report projection writes and what the research service reads."""

    async def cited_sources(self, run_id: uuid.UUID) -> dict[uuid.UUID, CitedSource]:
        """This run's sources, in the shape a reference entry needs.

        Unscoped by user, like the evidence store's ``fingerprints``: the caller
        is a projection running for a run the worker handed it, and the run id
        is the scope.
        """
        ...

    async def record_report(
        self,
        report: ReportRecord,
        sections: Sequence[SectionRecord],
        citations: Sequence[CitationRecord],
    ) -> None:
        """Replace this run's report with the one just assembled.

        Replace, not merge: a repaired draft can drop a section and re-cite a
        different claim, so anything the new assembly does not contain must go.
        Upserting alone would leave the previous revision's sections behind,
        numbered into the middle of the new report.
        """
        ...

    async def report_for(self, run_id: uuid.UUID, *, user_id: uuid.UUID) -> ReportResponse | None:
        """The stored report with its sections and citations, or ``None``."""
        ...
