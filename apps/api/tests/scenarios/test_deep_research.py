"""Scenario 1: a deep research run that works, end to end.

The one scenario the other fourteen are departures from, and the only test in
this repository that drives the whole vertical slice - queue, worker, lease,
graph, nine real agents, a real toolbelt over a scripted socket, ingestion,
retrieval, the evidence projection, report assembly and the citation check -
against real Postgres.

What it asserts is the product's central promise, walked rather than assumed: a
question goes in as a queued row, and what comes out is a report whose citation
leads to a claim, which leads to an evidence span, which leads to a document
this run actually fetched, at offsets that still hold the quoted words.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.enums import ReportSectionKind, RunStatus
from app.db.models.evidence import ClaimRow, EvidenceRow
from app.db.models.report import CitationRow, ReportRow, ReportSectionRow
from app.db.models.source import DocumentRow, SourceRow
from tests.scenarios import story
from tests.scenarios.catalogue import Scenario, covers
from tests.scenarios.world import queue_run, run_until, scripted_dns
from tests.support.worker import read_row, settled


@covers(Scenario.SUCCESSFUL_DEEP_RESEARCH)
async def test_a_question_becomes_a_report_whose_citations_lead_back_to_a_fetched_page(
    make_world, database
):
    world = make_world(web=story.web(), brain=story.ordinary_run())

    with scripted_dns():
        run = await queue_run(world, question=story.QUESTION)
        await run_until(world, settled(world.harness, database, run.id, RunStatus.COMPLETED))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.COMPLETED, row.error
    assert float(row.progress) == 1.0
    assert row.source_count == 1 and row.claim_count == 1
    assert row.error is None

    async with database.session() as session:
        report = (
            await session.execute(select(ReportRow).where(ReportRow.run_id == run.id))
        ).scalar_one()
        section = (
            await session.execute(
                select(ReportSectionRow).where(
                    ReportSectionRow.report_id == report.id,
                    ReportSectionRow.kind == ReportSectionKind.EXECUTIVE_SUMMARY.value,
                )
            )
        ).scalar_one()
        citation = (
            await session.execute(
                select(CitationRow).where(CitationRow.report_section_id == section.id)
            )
        ).scalar_one()
        claim = await session.get(ClaimRow, citation.claim_id)
        evidence = (
            await session.execute(select(EvidenceRow).where(EvidenceRow.claim_id == claim.id))
        ).scalar_one()
        source = await session.get(SourceRow, citation.source_id)
        document = await session.get(DocumentRow, evidence.document_id)

    # The chain, walked one link at a time rather than trusted to the validator
    # that built it: a citation that resolves in memory and not in the database
    # is a report nobody can open.
    assert f"[{citation.ordinal}]" in section.content_md
    assert claim.run_id == run.id
    assert source.url == story.PRICE_LIST.url
    assert document.source_id == source.id
    quoted = story.QUOTES[story.PRICE_LIST.url]
    assert evidence.span_text == quoted
    assert document.normalized_content[evidence.span_start : evidence.span_end] == quoted, (
        "the span must still bracket its words in the stored document, because that "
        "is what the citation validator re-read and what a reader will be shown"
    )

    # The citation check's own verdict, stored with the report: one marker
    # checked, one valid, nothing rejected.
    assert report.validation == {"checked": 1, "valid": 1, "rejected": 0, "reasons": []}


@covers(Scenario.SUCCESSFUL_DEEP_RESEARCH)
async def test_the_page_the_run_cited_is_one_it_fetched_through_the_guarded_client(
    make_world, database
):
    """The corpus is what the run gathered, not what the fixture declared.

    Two pages exist on the scripted web and the selector picks one, so a system
    that ingested everything it saw - or that cited a page it never read - shows
    up here as a source count that does not match the fetch log.
    """
    world = make_world(web=story.web(), brain=story.ordinary_run())

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, RunStatus.COMPLETED))

    assert world.web.fetches == [story.PRICE_LIST.url], "one page chosen, one page fetched"
    assert story.MARKET_REVIEW.url not in world.web.fetches
    # robots.txt was consulted before the page, as TDD 9.2 requires.
    assert f"{story.PRICE_LIST.url.rsplit('/', 1)[0]}/robots.txt" in world.web.requested

    async with database.session() as session:
        urls = (
            await session.execute(select(SourceRow.url).where(SourceRow.run_id == run.id))
        ).scalars()
    assert list(urls) == [story.PRICE_LIST.url]


@covers(Scenario.SUCCESSFUL_DEEP_RESEARCH)
async def test_every_model_call_the_run_made_is_in_the_ledger_with_its_cost(make_world, database):
    """FR-9, as rows rather than as an estimate: what the run spent is what its
    calls cost, counted from the ledger and never tallied a second way."""
    world = make_world(web=story.web(), brain=story.ordinary_run())

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, RunStatus.COMPLETED))

    row = await read_row(database, run.id)
    recorded = world.calls.calls
    assert len(recorded) == world.brain.calls > 0
    assert all(call.cost_usd is not None for call in recorded), (
        "a priced registry must price every call; an unpriced one is refused under a budget"
    )
    # To four decimal places, which is what ``total_cost_usd`` is declared to
    # hold. Asserted at the column's own precision rather than at the ledger's,
    # because a run row that claimed more precision than it stores would be
    # reporting a number the database cannot represent.
    ledger_total = sum(call.cost_usd or 0.0 for call in recorded)
    assert float(row.total_cost_usd) == pytest.approx(ledger_total, abs=5e-5)
    assert row.total_tokens == sum(
        (call.prompt_tokens or 0) + (call.completion_tokens or 0) for call in recorded
    )

    async with database.session() as session:
        from app.db.models.trace import AgentRunRow, LlmCallRow

        spans = await session.scalar(
            select(func.count()).select_from(AgentRunRow).where(AgentRunRow.run_id == run.id)
        )
        ledger = await session.scalar(
            select(func.count()).select_from(LlmCallRow).where(LlmCallRow.run_id == run.id)
        )
    assert spans > 0, "one trace row per node execution"
    assert ledger == len(recorded), "and every model call hangs from one of them"
