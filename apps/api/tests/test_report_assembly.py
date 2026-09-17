"""Turning a draft into a report: renumbering, assembly and the numbers on it.

Pure functions, so these tests can be exhaustive about the decisions a reader
depends on: that `[n]` means what the reference list says it means, that a marker
which resolves to nothing never silently becomes someone else's source, and that
a quoted page cannot smuggle a citation marker into the report.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.agents.catalog import Catalog
from app.agents.schemas import (
    CitationCheck,
    ClaimItem,
    RejectionCount,
    ReportDraft,
    ReportSectionDraft,
)
from app.core.enums import ClaimStatus, ClaimType, ReportSectionKind, ReportStatus
from app.reports.assembly import (
    UNRESOLVED_ORDINAL,
    assemble,
    escape_markdown,
    report_identity,
)
from app.reports.repository import CitedSource
from tests.support import agents as fake

RUN = uuid.UUID("22222222-2222-4222-8222-222222222222")
NOW = dt.datetime(2026, 9, 16, 12, 0, tzinfo=dt.UTC)


def claim(
    name: str, *, text: str, key: str, confidence: float, evidence_id: uuid.UUID
) -> ClaimItem:
    return ClaimItem(
        id=fake.ident(name),
        normalized_key=key,
        text=text,
        claim_type=ClaimType.QUANTITATIVE,
        status=ClaimStatus.VERIFIED,
        confidence=confidence,
        evidence_ids=(evidence_id,),
        object_value="",
    )


def source(name: str, *, title: str = "A report", publisher: str = "Example News") -> CitedSource:
    return CitedSource(
        id=fake.ident(name),
        title=title,
        url=f"https://{name}.test/story",
        publisher=publisher,
        accessed_at=NOW,
    )


def draft(*sections: ReportSectionDraft, caveat: str | None = None) -> ReportDraft:
    return ReportDraft(
        title="Inference pricing",
        model="test-writer-v1",
        revision=0,
        sections=sections or (section(ReportSectionKind.EXECUTIVE_SUMMARY, "Prices vary [1]."),),
        coverage_caveat=caveat,
    )


def section(kind: ReportSectionKind, content: str, heading: str = "Heading") -> ReportSectionDraft:
    return ReportSectionDraft(kind=kind, heading=heading, content_md=content, claim_ids=())


def check(*, checked: int = 1, valid: int = 1, rejected: int = 0) -> CitationCheck:
    return CitationCheck(
        revision=0,
        checked=checked,
        valid=valid,
        rejected=rejected,
        rejections=((RejectionCount(reason="no_such_claim", count=rejected),) if rejected else ()),
        repair_instructions="fix it" if rejected else None,
    )


def build(
    *sections: ReportSectionDraft,
    claims: tuple[ClaimItem, ...] | None = None,
    spans: dict[uuid.UUID, object] | None = None,
    sources: dict[uuid.UUID, CitedSource] | None = None,
    retrieved: set[uuid.UUID] | None = None,
    verdict: CitationCheck | None = None,
    caveat: str | None = None,
):
    """One assembly over a single claim backed by a single retrieved source."""
    span = fake.evidence("ev-1", quote="Prices fell to $2.80 per GPU-hour.")
    only = claim(
        "claim-1",
        text="Inference costs $2.80 per GPU-hour.",
        key="provider a | price | 2026",
        confidence=0.8,
        evidence_id=span.id,
    )
    return assemble(
        draft(*sections, caveat=caveat),
        run_id=RUN,
        claims=Catalog(claims if claims is not None else (only,)),
        evidence=spans if spans is not None else {span.id: span},
        sources=sources if sources is not None else {span.source_id: source("source-a")},
        retrieved=retrieved if retrieved is not None else {span.source_id},
        check=verdict if verdict is not None else check(),
        now=NOW,
    )


# --- renumbering ------------------------------------------------------------------


def test_a_claim_number_becomes_a_citation_ordinal():
    built = build(section(ReportSectionKind.EXECUTIVE_SUMMARY, "Prices fell [1]."))

    body = next(s for s in built.sections if s.kind is ReportSectionKind.EXECUTIVE_SUMMARY)
    assert body.content_md == "Prices fell [1]."
    assert [citation.ordinal for citation in built.citations] == [1]
    assert built.citations[0].quote == "Prices fell to $2.80 per GPU-hour."


def test_ordinals_are_assigned_in_the_order_the_report_first_uses_them():
    first = fake.evidence("ev-1", quote="Prices fell to $2.80 per GPU-hour.")
    second = fake.evidence("ev-2", quote="Capacity doubled in the quarter.", source_name="source-b")
    claims = (
        claim(
            "claim-1",
            text="Prices fell.",
            key="a | price | 2026",
            confidence=0.9,
            evidence_id=first.id,
        ),
        claim(
            "claim-2",
            text="Capacity doubled.",
            key="b | capacity | 2026",
            confidence=0.7,
            evidence_id=second.id,
        ),
    )

    built = build(
        # The prose cites claim 2 before claim 1: a reference list is numbered by
        # first use, not by the order the claims happened to be catalogued in.
        section(ReportSectionKind.EXECUTIVE_SUMMARY, "Capacity doubled [2], and prices fell [1]."),
        claims=claims,
        spans={first.id: first, second.id: second},
        sources={first.source_id: source("source-a"), second.source_id: source("source-b")},
        retrieved={first.source_id, second.source_id},
    )

    body = next(s for s in built.sections if s.kind is ReportSectionKind.EXECUTIVE_SUMMARY)
    assert body.content_md == "Capacity doubled [1], and prices fell [2]."
    assert {(c.ordinal, c.claim_id) for c in built.citations} == {
        (1, claims[1].id),
        (2, claims[0].id),
    }


def test_the_same_claim_cited_twice_keeps_one_ordinal_and_one_row_per_section():
    built = build(
        section(ReportSectionKind.EXECUTIVE_SUMMARY, "Prices fell [1], and fell again [1]."),
    )

    assert len(built.citations) == 1, "the row is the link between a section and a claim"
    body = next(s for s in built.sections if s.kind is ReportSectionKind.EXECUTIVE_SUMMARY)
    assert body.content_md == "Prices fell [1], and fell again [1]."


def test_a_marker_that_resolves_to_nothing_becomes_a_visibly_broken_one():
    built = build(section(ReportSectionKind.EXECUTIVE_SUMMARY, "Something happened [9]."))

    body = next(s for s in built.sections if s.kind is ReportSectionKind.EXECUTIVE_SUMMARY)
    assert body.content_md == f"Something happened [{UNRESOLVED_ORDINAL}].", (
        "leaving [9] in place would point the reader at whichever source is ninth"
    )
    assert built.citations == ()


def test_a_claim_whose_source_was_never_retrieved_cannot_be_cited():
    span = fake.evidence("ev-1", quote="Prices fell to $2.80 per GPU-hour.")
    built = build(
        section(ReportSectionKind.EXECUTIVE_SUMMARY, "Prices fell [1]."),
        spans={span.id: span},
        retrieved=set(),
    )

    body = next(s for s in built.sections if s.kind is ReportSectionKind.EXECUTIVE_SUMMARY)
    assert body.content_md == f"Prices fell [{UNRESOLVED_ORDINAL}]."
    assert built.citations == ()


def test_a_markdown_link_whose_label_is_a_number_is_left_alone():
    built = build(
        section(ReportSectionKind.EXECUTIVE_SUMMARY, "See [1](https://example.test) and [1]."),
    )

    body = next(s for s in built.sections if s.kind is ReportSectionKind.EXECUTIVE_SUMMARY)
    assert body.content_md == "See [1](https://example.test) and [1]."


# --- assembled sections -----------------------------------------------------------


def test_evidence_and_references_are_assembled_from_records():
    built = build(section(ReportSectionKind.EXECUTIVE_SUMMARY, "Prices fell [1]."))

    kinds = [s.kind for s in built.sections]
    assert ReportSectionKind.EVIDENCE in kinds
    assert ReportSectionKind.REFERENCES in kinds
    evidence = next(s for s in built.sections if s.kind is ReportSectionKind.EVIDENCE)
    references = next(s for s in built.sections if s.kind is ReportSectionKind.REFERENCES)
    assert "Prices fell to $2.80 per GPU-hour." in evidence.content_md
    assert "https://source-a.test/story" in references.content_md
    assert "Accessed 2026-09-16" in references.content_md


def test_two_claims_from_one_source_are_one_reference_cited_twice():
    first = fake.evidence("ev-1", quote="Prices fell to $2.80 per GPU-hour.")
    second = fake.evidence("ev-2", quote="Capacity doubled in the quarter.")
    claims = (
        claim(
            "claim-1",
            text="Prices fell.",
            key="a | price | 2026",
            confidence=0.9,
            evidence_id=first.id,
        ),
        claim(
            "claim-2",
            text="Capacity doubled.",
            key="b | capacity | 2026",
            confidence=0.7,
            evidence_id=second.id,
        ),
    )

    built = build(
        section(ReportSectionKind.EXECUTIVE_SUMMARY, "Prices fell [1] and capacity doubled [2]."),
        claims=claims,
        spans={first.id: first, second.id: second},
    )

    references = next(s for s in built.sections if s.kind is ReportSectionKind.REFERENCES)
    assert references.content_md.count("https://source-a.test/story") == 1, (
        "listing one page twice would overstate how many sources the report rests on"
    )
    assert "[1], [2]" in references.content_md


def test_a_report_that_cites_nothing_gets_no_evidence_or_reference_sections():
    built = build(section(ReportSectionKind.EXECUTIVE_SUMMARY, "Something happened [9]."))

    kinds = [s.kind for s in built.sections]
    assert ReportSectionKind.EVIDENCE not in kinds
    assert ReportSectionKind.REFERENCES not in kinds


def test_sections_are_numbered_in_the_order_a_report_reads_in():
    built = build(
        section(ReportSectionKind.RECOMMENDATIONS, "Do this [1]."),
        section(ReportSectionKind.EXECUTIVE_SUMMARY, "Prices fell [1]."),
    )

    ordered = [(s.ordinal, s.kind) for s in built.sections]
    assert ordered == sorted(ordered), "FR-9's order is the enum's, not the writer's"
    assert ordered[0][1] is ReportSectionKind.EXECUTIVE_SUMMARY
    assert ordered[-1][1] is ReportSectionKind.REFERENCES


# --- what is written on the report ------------------------------------------------


def test_a_quoted_page_cannot_smuggle_a_citation_marker_into_the_report():
    span = fake.evidence("ev-1", quote="The filing listed [3] pending matters.")
    built = build(
        section(ReportSectionKind.EXECUTIVE_SUMMARY, "Matters are pending [1]."),
        spans={span.id: span},
    )

    evidence = next(s for s in built.sections if s.kind is ReportSectionKind.EVIDENCE)
    assert "\\[3]" in evidence.content_md, "escaped, so the renderer shows it as text"
    assert "[3]" not in evidence.content_md.replace("\\[3]", ""), (
        "an unescaped [3] would render as a citation to whichever source is third"
    )


def test_escaping_only_touches_the_sequence_that_would_be_misread():
    assert escape_markdown("a [3] b") == "a \\[3] b"
    assert escape_markdown("a [note] b") == "a [note] b", "only digits make a marker"
    assert escape_markdown("**bold** stays") == "**bold** stays"


def test_confidence_is_the_mean_of_the_claims_the_report_cites():
    first = fake.evidence("ev-1", quote="Prices fell to $2.80 per GPU-hour.")
    second = fake.evidence("ev-2", quote="Capacity doubled in the quarter.")
    claims = (
        claim(
            "claim-1",
            text="Prices fell.",
            key="a | price | 2026",
            confidence=0.9,
            evidence_id=first.id,
        ),
        claim(
            "claim-2",
            text="Capacity doubled.",
            key="b | capacity | 2026",
            confidence=0.5,
            evidence_id=second.id,
        ),
    )

    built = build(
        section(ReportSectionKind.EXECUTIVE_SUMMARY, "Prices fell [1] and capacity doubled [2]."),
        claims=claims,
        spans={first.id: first, second.id: second},
    )

    assert built.report.overall_confidence == pytest.approx(0.7)


def test_a_report_that_cites_nothing_has_no_confidence_rather_than_a_placeholder():
    built = build(section(ReportSectionKind.EXECUTIVE_SUMMARY, "Something happened [9]."))

    assert built.report.overall_confidence is None, (
        "the mean of nothing is not 0.50, which is what the column used to default to"
    )


def test_a_draft_whose_citations_were_rejected_is_stored_as_a_draft():
    built = build(
        section(ReportSectionKind.EXECUTIVE_SUMMARY, "Something happened [9]."),
        verdict=check(checked=1, valid=0, rejected=1),
    )

    assert built.report.status is ReportStatus.DRAFT
    assert built.report.validated_at == NOW, "the check ran; it failed"
    assert built.report.validation is not None
    assert built.report.validation.reasons == (("no_such_claim", 1),)


def test_a_clean_draft_is_stored_as_validated():
    built = build(section(ReportSectionKind.EXECUTIVE_SUMMARY, "Prices fell [1]."))

    assert built.report.status is ReportStatus.VALIDATED
    assert built.report.model == "test-writer-v1"


def test_the_summary_is_plain_text_with_no_markers_in_it():
    built = build(
        section(ReportSectionKind.EXECUTIVE_SUMMARY, "Prices fell [1] sharply."),
    )

    assert built.report.summary == "Prices fell sharply.", (
        "the summary is shown where citations are not loaded, so a marker there "
        "would render as a broken one"
    )


def test_the_caveat_a_limit_required_survives_onto_the_report():
    built = build(
        section(ReportSectionKind.EXECUTIVE_SUMMARY, "Prices fell [1]."),
        caveat="Research stopped at the source limit.",
    )

    assert built.report.coverage_caveat == "Research stopped at the source limit."


def test_the_report_id_is_derived_from_the_run():
    built = build(section(ReportSectionKind.EXECUTIVE_SUMMARY, "Prices fell [1]."))

    assert built.report.id == report_identity(RUN)
    assert all(section.report_id == built.report.id for section in built.sections)
