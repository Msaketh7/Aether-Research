"""Turning a validated draft into the report a reader opens (FR-9, Phase 12).

The synthesizer writes prose and cites claims by their catalogue number, because
a model that emits identifiers can fabricate them (ADR 0015). A reader needs
something else: `[n]` that resolves to a *source* and the exact quote behind the
sentence. This is where one becomes the other, and nothing here calls a model.

**Markers are renumbered, not trusted.** Every `[n]` in the draft is resolved
through the same catalogue the synthesizer was shown, then rewritten to a
citation ordinal assigned in first-appearance order - which is how a numbered
reference list has worked since long before this system. A marker that does not
resolve is rewritten to `[0]`, an ordinal no citation can have, so the frontend
renders it as a visible broken citation. Leaving the original number in place
would be worse than deleting it: `[7]` in a report that has seven citations
would quietly become someone else's source.

**Two sections are assembled rather than written.** The synthesizer is refused
the Evidence and References sections (`app.agents.synthesis`), because a model
writing a list of sources is the most reliable way to obtain citations to
documents that do not exist. They are built here from the run's own rows.

**Verbatim text is escaped before it is embedded.** Evidence quotes and source
titles come from fetched pages, and a page containing "[3]" would otherwise
render inside the report as a citation marker pointing at whatever claim 3 is.
The markdown renderer takes a backslash escape for exactly this reason
(`apps/web/src/lib/research/markdown.ts`); everything untrusted goes through
`escape_markdown` on its way in. Threat model 3.1: retrieved content is data.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.agents.catalog import Catalog
from app.agents.citations import CITATION_MARKER
from app.agents.schemas import (
    CitationCheck,
    ClaimItem,
    EvidenceItem,
    ReportDraft,
    ReportSectionDraft,
)
from app.core.enums import ReportSectionKind, ReportStatus
from app.reports.repository import (
    CitationRecord,
    CitedSource,
    ReportRecord,
    SectionRecord,
    ValidationRecord,
)

#: Namespaces for the derived ids. A run has one report, a report one section per
#: kind, and a section one citation per claim - so each id is a UUID5 over what
#: makes it unique, and re-projecting a run rewrites its rows rather than
#: accumulating copies of them (ADR 0016).
_REPORT_NAMESPACE = uuid.UUID("7a1c2f48-6de1-4b93-9d20-5c8f3a17e4b6")
_SECTION_NAMESPACE = uuid.UUID("c3b8e5d2-04a7-4f16-8e93-1b6d7c20af55")
_CITATION_NAMESPACE = uuid.UUID("2e9d4b71-8c35-4a08-b6f2-9a4e1d73c580")

#: The ordinal given to a marker that resolves to nothing. No citation can hold
#: it (``Citation.ordinal`` is ``ge=1``), so the reader's renderer shows it as an
#: unresolved citation instead of resolving it to an unrelated source.
UNRESOLVED_ORDINAL = 0

#: Characters of the executive summary kept as the report's plain-text summary.
SUMMARY_CHARS = 320

#: Sources one reference list will name. A report cites tens; the bound is here
#: so a bug upstream truncates a list rather than writing a section of unbounded
#: size into a text column.
MAX_REFERENCES = 200


@dataclass(frozen=True, slots=True)
class AssembledReport:
    """Everything one projection writes, already in row shape."""

    report: ReportRecord
    sections: tuple[SectionRecord, ...]
    citations: tuple[CitationRecord, ...]


@dataclass(frozen=True, slots=True)
class _Assembled:
    """A section built from the run's records rather than written by a model."""

    kind: ReportSectionKind
    heading: str
    content_md: str


@dataclass(frozen=True, slots=True)
class _Resolved:
    """One claim the report cites, and the source the citation points at."""

    ordinal: int
    claim: ClaimItem
    source: CitedSource
    quote: str


def report_identity(run_id: uuid.UUID) -> uuid.UUID:
    """A run has one report, so its id is derived from the run."""
    return uuid.uuid5(_REPORT_NAMESPACE, str(run_id))


def section_identity(report_id: uuid.UUID, kind: ReportSectionKind) -> uuid.UUID:
    return uuid.uuid5(_SECTION_NAMESPACE, f"{report_id}:{kind.value}")


def citation_identity(section_id: uuid.UUID, claim_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(_CITATION_NAMESPACE, f"{section_id}:{claim_id}")


def escape_markdown(text: str) -> str:
    """Neutralise the marker shape in text that came from a fetched page.

    Only `[` before digits needs escaping: it is the one sequence the reader's
    renderer turns into something other than text. Bold, italic and code markers
    render as formatting at worst, which is ugly rather than misleading, and
    escaping them all would fill quotations with backslashes.
    """
    return re.sub(r"\[(?=\d)", r"\\[", text)


def assemble(
    draft: ReportDraft,
    *,
    run_id: uuid.UUID,
    claims: Catalog[ClaimItem],
    evidence: Mapping[uuid.UUID, EvidenceItem],
    sources: Mapping[uuid.UUID, CitedSource],
    retrieved: set[uuid.UUID],
    check: CitationCheck | None,
    now: dt.datetime,
) -> AssembledReport:
    """One draft, its citations resolved and its assembled sections built.

    ``claims`` must be the catalogue the synthesizer was shown, rebuilt from the
    same state - it is what the draft's numbers mean. The citation validator
    rebuilds it too, and a difference between the three would point every marker
    in the report at the wrong claim while still validating (ADR 0015).
    """
    report_id = report_identity(run_id)
    resolver = _Resolver(claims=claims, evidence=evidence, sources=sources, retrieved=retrieved)

    written: list[tuple[ReportSectionDraft, str, tuple[_Resolved, ...]]] = []
    for section in draft.sections:
        content, cited = resolver.rewrite(section.content_md)
        written.append((section, content, cited))

    resolved = resolver.resolved
    # Omitted entirely when the report cites nothing: an Evidence section with no
    # evidence in it tells a reader less than its absence does.
    assembled = [
        section
        for section in (_evidence_section(resolved), _references_section(resolved))
        if section is not None
    ]

    sections: list[SectionRecord] = []
    citations: list[CitationRecord] = []
    for section, content, cited in written:
        sections.append(
            SectionRecord(
                id=section_identity(report_id, section.kind),
                report_id=report_id,
                kind=section.kind,
                heading=section.heading,
                ordinal=0,  # replaced below, once every section is in order
                content_md=content,
            )
        )
        citations.extend(_citation(sections[-1].id, entry) for entry in _distinct(cited))
    for built in assembled:
        sections.append(
            SectionRecord(
                id=section_identity(report_id, built.kind),
                report_id=report_id,
                kind=built.kind,
                heading=built.heading,
                ordinal=0,
                content_md=built.content_md,
            )
        )

    ordered = tuple(
        SectionRecord(
            id=section.id,
            report_id=section.report_id,
            kind=section.kind,
            heading=section.heading,
            ordinal=position,
            content_md=section.content_md,
        )
        for position, section in enumerate(sorted(sections, key=_section_order), start=1)
    )
    return AssembledReport(
        report=_report_record(
            draft,
            report_id=report_id,
            run_id=run_id,
            sections=ordered,
            resolved=resolved,
            check=check,
            now=now,
        ),
        sections=ordered,
        citations=tuple(citations),
    )


class _Resolver:
    """Assigns citation ordinals, in the order the report first uses them."""

    def __init__(
        self,
        *,
        claims: Catalog[ClaimItem],
        evidence: Mapping[uuid.UUID, EvidenceItem],
        sources: Mapping[uuid.UUID, CitedSource],
        retrieved: set[uuid.UUID],
    ) -> None:
        self._claims = claims
        self._evidence = evidence
        self._sources = sources
        self._retrieved = retrieved
        self._by_claim: dict[uuid.UUID, _Resolved] = {}

    @property
    def resolved(self) -> tuple[_Resolved, ...]:
        """Every citation the report makes, in ordinal order."""
        return tuple(sorted(self._by_claim.values(), key=lambda entry: entry.ordinal))

    def rewrite(self, markdown: str) -> tuple[str, tuple[_Resolved, ...]]:
        """The section's text with its markers renumbered, and what they cite."""
        cited: list[_Resolved] = []

        def replace(match: re.Match[str]) -> str:
            after = markdown[match.end() : match.end() + 1]
            if after in ("(", "["):
                # A Markdown link whose label is a number, not a citation - the
                # same exclusion the validator counts by (``cited_numbers``).
                return match.group(0)
            entry = self._resolve(int(match.group(1)))
            if entry is None:
                return f"[{UNRESOLVED_ORDINAL}]"
            cited.append(entry)
            return f"[{entry.ordinal}]"

        return CITATION_MARKER.sub(replace, markdown), tuple(cited)

    def _resolve(self, marker: int) -> _Resolved | None:
        """The citation for a claim number, assigning an ordinal on first use.

        Returns ``None`` when any link of the chain is missing: the number names
        no claim, the claim has no evidence, or none of its evidence belongs to a
        source this run retrieved and stored. Those are the validator's four
        rejection reasons, re-checked here against the rows rather than the
        state - the two agree, and this is the one that decides what is written.
        """
        claim = self._claims.get(marker)
        if claim is None:
            return None
        if (existing := self._by_claim.get(claim.id)) is not None:
            return existing
        for evidence_id in claim.evidence_ids:
            span = self._evidence.get(evidence_id)
            if span is None or span.source_id not in self._retrieved:
                continue
            source = self._sources.get(span.source_id)
            if source is None:
                continue
            entry = _Resolved(
                ordinal=len(self._by_claim) + 1,
                claim=claim,
                source=source,
                quote=span.claim_text,
            )
            self._by_claim[claim.id] = entry
            return entry
        return None


def _citation(section_id: uuid.UUID, entry: _Resolved) -> CitationRecord:
    return CitationRecord(
        id=citation_identity(section_id, entry.claim.id),
        report_section_id=section_id,
        claim_id=entry.claim.id,
        source_id=entry.source.id,
        ordinal=entry.ordinal,
        quote=entry.quote,
        confidence=entry.claim.confidence,
    )


def _distinct(cited: Sequence[_Resolved]) -> list[_Resolved]:
    """One citation row per claim per section, however often it is cited.

    The row is the link between a section and a claim; the marker can repeat in
    the prose, and every repeat resolves through the same ordinal.
    """
    seen: dict[uuid.UUID, _Resolved] = {}
    for entry in cited:
        seen.setdefault(entry.claim.id, entry)
    return list(seen.values())


def _evidence_section(resolved: Sequence[_Resolved]) -> _Assembled | None:
    """The quote behind every citation, in ordinal order.

    Assembled from records, so a reader can check the report without leaving it:
    each entry is the claim, the marker that cites it, and the verbatim span.
    """
    if not resolved:
        return None
    lines = [
        "Every citation in this report, with the exact passage it rests on.",
        "",
    ]
    for entry in resolved:
        lines.append(
            f"{entry.ordinal}. **{escape_markdown(entry.claim.text)}** "
            f"[{entry.ordinal}] — {escape_markdown(entry.source.publisher)}"
        )
        lines.append(f'   "{escape_markdown(entry.quote)}"')
    return _Assembled(ReportSectionKind.EVIDENCE, "Evidence", "\n".join(lines))


def _references_section(resolved: Sequence[_Resolved]) -> _Assembled | None:
    """The sources cited, each naming the markers that point at it.

    One entry per source rather than per marker: two claims drawn from the same
    page are one reference, cited twice, and a list that repeated the page under
    two numbers would overstate how many sources the report rests on.
    """
    if not resolved:
        return None
    by_source: dict[uuid.UUID, list[_Resolved]] = {}
    for entry in resolved:
        by_source.setdefault(entry.source.id, []).append(entry)

    lines: list[str] = []
    for entries in list(by_source.values())[:MAX_REFERENCES]:
        source = entries[0].source
        markers = ", ".join(f"[{entry.ordinal}]" for entry in entries)
        accessed = source.accessed_at.date().isoformat()
        lines.append(
            f"- {markers} {escape_markdown(source.title)} — "
            f"{escape_markdown(source.publisher)}. Accessed {accessed}. "
            f"{escape_markdown(source.url)}"
        )
    return _Assembled(ReportSectionKind.REFERENCES, "References", "\n".join(lines))


def _section_order(section: SectionRecord) -> int:
    """FR-9's order, which is the enum's declaration order."""
    return _KIND_ORDER[section.kind]


_KIND_ORDER = {kind: position for position, kind in enumerate(ReportSectionKind)}


def _report_record(
    draft: ReportDraft,
    *,
    report_id: uuid.UUID,
    run_id: uuid.UUID,
    sections: Sequence[SectionRecord],
    resolved: Sequence[_Resolved],
    check: CitationCheck | None,
    now: dt.datetime,
) -> ReportRecord:
    return ReportRecord(
        id=report_id,
        run_id=run_id,
        title=draft.title,
        summary=_summary(sections),
        overall_confidence=_confidence(resolved),
        # Validated is what the validator said, not what the writer hoped: a
        # draft that shipped with rejected citations is stored as a draft, and
        # the API serves it with the count of what did not resolve.
        status=(
            ReportStatus.VALIDATED if check is not None and check.passed else ReportStatus.DRAFT
        ),
        model=draft.model or "unknown",
        word_count=sum(len(section.content_md.split()) for section in sections),
        generated_at=now,
        validated_at=now if check is not None else None,
        coverage_caveat=draft.coverage_caveat,
        validation=_validation(check),
    )


def _validation(check: CitationCheck | None) -> ValidationRecord | None:
    """The validator's verdict, or ``None`` when it never ran."""
    if check is None:
        return None
    return ValidationRecord(
        checked=check.checked,
        valid=check.valid,
        rejected=check.rejected,
        reasons=tuple((rejection.reason, rejection.count) for rejection in check.rejections),
    )


def _summary(sections: Iterable[SectionRecord]) -> str:
    """The executive summary as plain text, for list views.

    Markers are stripped rather than kept: this is shown where the citations are
    not loaded, and a marker there resolves to nothing and renders as a broken
    citation on a page that is not even showing the report.
    """
    for section in sections:
        if section.kind is not ReportSectionKind.EXECUTIVE_SUMMARY:
            continue
        plain = " ".join(CITATION_MARKER.sub("", section.content_md).split())
        if len(plain) <= SUMMARY_CHARS:
            return plain
        return plain[:SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"
    return ""


def _confidence(resolved: Sequence[_Resolved]) -> float | None:
    """Mean confidence of the distinct claims the report cites.

    ``None`` when it cites none: the mean of nothing is not zero, and the column
    used to default to 0.50, which a reader sees as a measurement the system
    made. A report in that state is stored as a draft whose validation summary
    says every citation was rejected.
    """
    if not resolved:
        return None
    return round(sum(entry.claim.confidence for entry in resolved) / len(resolved), 2)
