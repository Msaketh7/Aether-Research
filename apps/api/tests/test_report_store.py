"""A report in Postgres: projecting one, rewriting it, and reading it back.

Real database, because what is under test is what the schema enforces. The
citations table has foreign keys to ``claims`` and ``sources`` declared
``ON DELETE RESTRICT`` - the database saying the product's promise out loud - so
a citation to a claim that was never written cannot be inserted at all, and the
only way to find out whether the projection gets the order right is to run it.

The state is built by hand; ``test_report_assembly`` covers what the assembly
decides, and ``test_agents_end_to_end`` covers the graph that produces the draft.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.agents.schemas import (
    CitationCheck,
    RejectionCount,
    ReportDraft,
    ReportSectionDraft,
)
from app.core.enums import ReportSectionKind, ReportStatus
from app.db.repositories.reports import SqlAlchemyReportRepository
from app.db.session import Database
from app.reports.assembly import UNRESOLVED_ORDINAL, report_identity
from app.research.recorder import RunRecorder
from tests.support import agents as fake
from tests.support.projection import WIRE, count, seed_run, seed_source, seed_user

pytestmark = pytest.mark.anyio

QUOTE = "Inference on H100 instances is priced at $4.10 per GPU-hour."


@pytest.fixture
async def owner(database: Database) -> uuid.UUID:
    return await seed_user(database)


@pytest.fixture
async def run_id(database: Database, owner: uuid.UUID) -> uuid.UUID:
    return await seed_run(database, owner)


@pytest.fixture
async def recorder(database: Database) -> RunRecorder:
    return RunRecorder(database)


def draft(*contents: str, title: str = "Inference pricing") -> ReportDraft:
    """A draft whose first section is the executive summary."""
    kinds = [
        ReportSectionKind.EXECUTIVE_SUMMARY,
        ReportSectionKind.KEY_FINDINGS,
        ReportSectionKind.DETAILED_ANALYSIS,
    ]
    return ReportDraft(
        title=title,
        model="test-writer-v1",
        revision=0,
        sections=tuple(
            ReportSectionDraft(
                kind=kind, heading=kind.value.replace("_", " ").title(), content_md=content
            )
            for kind, content in zip(kinds, contents, strict=False)
        ),
        coverage_caveat=None,
    )


def verdict(*, checked: int = 1, valid: int = 1, rejected: int = 0) -> CitationCheck:
    return CitationCheck(
        revision=0,
        checked=checked,
        valid=valid,
        rejected=rejected,
        rejections=((RejectionCount(reason="no_such_claim", count=rejected),) if rejected else ()),
        repair_instructions="rewrite the citations" if rejected else None,
    )


async def one_claim_run(
    database: Database, run_id: uuid.UUID, *, quote: str = QUOTE
) -> tuple[object, object]:
    """A run with one source, one span and one claim. Returns (span, claim)."""
    source_id, document_id = await seed_source(database, run_id, "a", excerpt=WIRE)
    span = fake.evidence("ev-1", quote=quote).model_copy(
        update={"source_id": source_id, "document_id": document_id}
    )
    return span, fake.claim("claim-1", evidence_ids=(span.id,))


def state(run_id: uuid.UUID, span, claim, *, report: ReportDraft, check: CitationCheck | None):
    return fake.state(
        research_id=run_id,
        evidence=[span],
        claims=[claim],
        sources=[fake.source("source-a").model_copy(update={"source_id": span.source_id})],
        report=report,
        citation_check=check,
    )


# --- projecting -------------------------------------------------------------------


async def test_a_validated_draft_becomes_a_report_with_sections_and_citations(
    database: Database, recorder: RunRecorder, run_id: uuid.UUID
):
    span, claim = await one_claim_run(database, run_id)

    recorded = await recorder.record(
        state(
            run_id,
            span,
            claim,
            report=draft("H100 inference is priced at $4.10 per GPU-hour [1]."),
            check=verdict(),
        )
    )

    assert recorded.citations == 1
    assert await count(database, "reports", run_id) == 1
    async with database.session() as session:
        row = (
            await session.execute(
                sa.text("SELECT id, status, model, overall_confidence FROM reports")
            )
        ).one()
        kinds = list(
            (
                await session.execute(sa.text("SELECT kind FROM report_sections ORDER BY ordinal"))
            ).scalars()
        )
    assert row.id == report_identity(run_id)
    assert row.status == ReportStatus.VALIDATED.value
    assert row.model == "test-writer-v1"
    assert float(row.overall_confidence) == pytest.approx(claim.confidence)
    assert kinds == ["executive_summary", "evidence", "references"], (
        "the two assembled sections are added, in the order a report reads in"
    )


async def test_a_citation_points_at_the_claim_and_the_source_it_was_checked_against(
    database: Database, recorder: RunRecorder, run_id: uuid.UUID
):
    span, claim = await one_claim_run(database, run_id)

    await recorder.record(
        state(
            run_id,
            span,
            claim,
            report=draft("H100 inference is priced at $4.10 per GPU-hour [1]."),
            check=verdict(),
        )
    )

    async with database.session() as session:
        row = (
            await session.execute(
                sa.text("SELECT claim_id, source_id, ordinal, quote, confidence FROM citations")
            )
        ).one()
    assert row.claim_id == claim.id
    assert row.source_id == span.source_id
    assert row.ordinal == 1
    assert row.quote == QUOTE, "the quote is stored with the citation, so it cannot drift"


async def test_the_database_refuses_a_citation_to_a_claim_that_was_never_written(
    database: Database, run_id: uuid.UUID
):
    """The last line of defence, and the reason the two projections share a
    transaction: the foreign key, not the code, is what makes a fabricated
    citation impossible to store."""
    source_id, _ = await seed_source(database, run_id, "a")
    report_id = report_identity(run_id)
    async with database.session() as session:
        await session.execute(
            sa.text(
                "INSERT INTO reports (id, run_id, title, status, model, generated_at) "
                "VALUES (:id, :run_id, 'T', 'draft', 'm', now())"
            ),
            {"id": report_id, "run_id": run_id},
        )
        section_id = uuid.uuid4()
        await session.execute(
            sa.text(
                "INSERT INTO report_sections (id, report_id, kind, heading, ordinal, content_md) "
                "VALUES (:id, :report_id, 'executive_summary', 'S', 1, 'x')"
            ),
            {"id": section_id, "report_id": report_id},
        )
        with pytest.raises(sa.exc.IntegrityError):
            await session.execute(
                sa.text(
                    "INSERT INTO citations "
                    "(id, report_section_id, claim_id, source_id, ordinal, quote) "
                    "VALUES (:id, :section, :claim, :source, 1, 'q')"
                ),
                {
                    "id": uuid.uuid4(),
                    "section": section_id,
                    "claim": uuid.uuid4(),
                    "source": source_id,
                },
            )


async def test_a_rewritten_report_replaces_the_previous_one(
    database: Database, recorder: RunRecorder, run_id: uuid.UUID
):
    # The repair loop can produce a second draft with fewer sections. The first
    # draft's extra section must not survive, numbered into the new report.
    span, claim = await one_claim_run(database, run_id)
    first = draft(
        "Prices are $4.10 per GPU-hour [1].",
        "More detail [1].",
        "Even more [1].",
    )
    await recorder.record(state(run_id, span, claim, report=first, check=verdict()))
    assert await count(database, "report_sections") == 5

    second = draft("Prices are $4.10 per GPU-hour [1].")
    await recorder.record(state(run_id, span, claim, report=second, check=verdict()))

    assert await count(database, "reports", run_id) == 1
    async with database.session() as session:
        kinds = list(
            (
                await session.execute(sa.text("SELECT kind FROM report_sections ORDER BY ordinal"))
            ).scalars()
        )
    assert kinds == ["executive_summary", "evidence", "references"]
    assert await count(database, "citations") == 1, "the dropped sections' citations went with them"


async def test_projecting_the_same_report_twice_writes_it_once(
    database: Database, recorder: RunRecorder, run_id: uuid.UUID
):
    span, claim = await one_claim_run(database, run_id)
    payload = state(run_id, span, claim, report=draft("Prices are $4.10 [1]."), check=verdict())

    await recorder.record(payload)
    await recorder.record(payload)

    assert await count(database, "reports", run_id) == 1
    assert await count(database, "report_sections") == 3
    assert await count(database, "citations") == 1


async def test_a_run_with_no_validated_draft_projects_no_report(
    database: Database, recorder: RunRecorder, run_id: uuid.UUID
):
    span, claim = await one_claim_run(database, run_id)

    recorded = await recorder.record(
        state(run_id, span, claim, report=draft("Prices are $4.10 [1]."), check=None)
    )

    assert recorded.sections is None
    assert await count(database, "reports", run_id) == 0, (
        "an unchecked draft behind the same endpoint as a checked one would make "
        "the endpoint meaningless"
    )
    assert recorded.evidence.claims == 1, "the evidence it did gather is still recorded"


# --- reading back -----------------------------------------------------------------


async def test_the_endpoint_serves_the_report_its_citations_and_the_verdict(
    database: Database, recorder: RunRecorder, run_id: uuid.UUID, owner: uuid.UUID
):
    span, claim = await one_claim_run(database, run_id)
    await recorder.record(
        state(
            run_id,
            span,
            claim,
            report=draft("H100 inference is priced at $4.10 per GPU-hour [1]."),
            check=verdict(),
        )
    )

    async with database.session() as session:
        response = await SqlAlchemyReportRepository(session).report_for(run_id, user_id=owner)

    assert response is not None
    assert response.report.status is ReportStatus.VALIDATED
    assert [section.kind for section in response.sections] == [
        ReportSectionKind.EXECUTIVE_SUMMARY,
        ReportSectionKind.EVIDENCE,
        ReportSectionKind.REFERENCES,
    ]
    [citation] = response.citations
    assert citation.ordinal == 1
    assert citation.quote == QUOTE
    assert citation.source_title == "Report from a", "joined from the source row"
    assert response.validation is not None
    assert (response.validation.checked, response.validation.rejected) == (1, 0)


async def test_a_report_that_shipped_with_rejections_says_so(
    database: Database, recorder: RunRecorder, run_id: uuid.UUID, owner: uuid.UUID
):
    span, claim = await one_claim_run(database, run_id)

    await recorder.record(
        state(
            run_id,
            span,
            claim,
            # A marker naming a claim the catalogue does not have: the repair
            # budget ran out and the draft shipped with it.
            report=draft("Something else entirely [9]."),
            check=verdict(checked=1, valid=0, rejected=1),
        )
    )
    async with database.session() as session:
        response = await SqlAlchemyReportRepository(session).report_for(run_id, user_id=owner)

    assert response is not None
    assert response.report.status is ReportStatus.DRAFT
    assert response.report.overall_confidence is None
    assert response.citations == []
    assert f"[{UNRESOLVED_ORDINAL}]" in response.sections[0].content_md, (
        "the broken marker survives as a visibly broken one rather than "
        "resolving to an unrelated source"
    )
    assert response.validation is not None
    assert [(r.reason, r.count) for r in response.validation.rejection_reasons] == [
        ("the citation named a claim this run does not have", 1)
    ], "the stored code is stable; what the reader is shown is a sentence"


async def test_another_users_report_is_not_readable(
    database: Database, recorder: RunRecorder, run_id: uuid.UUID
):
    span, claim = await one_claim_run(database, run_id)
    await recorder.record(
        state(run_id, span, claim, report=draft("Prices are $4.10 [1]."), check=verdict())
    )
    stranger = await seed_user(database)

    async with database.session() as session:
        response = await SqlAlchemyReportRepository(session).report_for(run_id, user_id=stranger)

    assert response is None, "ownership is a join, not a filter applied afterwards"
