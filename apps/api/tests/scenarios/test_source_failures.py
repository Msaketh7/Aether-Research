"""Scenarios 2, 6, 8 and 9: what a run does when the sources fail it.

Four different ways of finding nothing, and the system owes a different answer
to each. The search provider times out; the same page is found twice; pages are
read but nothing in them can be quoted; the search simply returns nothing. Only
the first of those is an outage, and only the last two are reasons to admit that
the question could not be answered.

The failures are injected at the socket, not at the tool. A double raising
``FetchTimeout`` would prove the researcher handles an exception someone chose
to raise; a socket that times out proves the client classifies a real one as
that exception first, which is the half of the path that can silently change.
"""

from __future__ import annotations

import httpx2 as httpx
from sqlalchemy import func, select

from app.agents.outputs import EvidenceCandidate, EvidenceOutput, SearchQueries
from app.core.enums import EvidenceStance, RunStatus, ToolName, ToolStatus
from app.db.models.evidence import ClaimRow
from app.db.models.report import ReportRow
from app.db.models.source import DocumentRow, SourceRow
from tests.scenarios import story
from tests.scenarios.catalogue import Scenario, covers
from tests.scenarios.world import queue_run, run_until, scripted_dns
from tests.support.worker import read_row, settled

TERMINAL = (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)


# --- the search provider goes away ------------------------------------------------


@covers(Scenario.SEARCH_PROVIDER_TIMEOUT)
async def test_one_search_that_times_out_does_not_cost_the_run_its_report(make_world, database):
    """A subtask asks two questions at once. One provider call never answers.

    The run must finish on what the other returned. A researcher that let a
    timeout propagate would lose the subtask, and a graph that let it propagate
    further would lose the run - over one slow HTTP request.
    """
    web = story.web()
    web.search_errors["capacity"] = httpx.ReadTimeout("the search provider did not answer")
    brain = story.ordinary_run()
    brain.on(SearchQueries, story.queries("h100 gpu-hour list price", "h100 capacity reservations"))
    world = make_world(web=web, brain=brain)

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, *TERMINAL))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.COMPLETED, row.error
    assert row.source_count == 1, "the query that answered still produced a source"

    async with database.session() as session:
        assert await session.scalar(select(ReportRow).where(ReportRow.run_id == run.id)) is not None

    timed_out = [
        call
        for call in world.telemetry.tools.calls
        if call.tool_name is ToolName.SEARCH and call.status is not ToolStatus.OK
    ]
    assert timed_out, "the failed search is a recorded tool call, not a silence"
    assert {call.error_code for call in timed_out} == {"fetch_timeout"}, (
        "a socket that timed out must reach the ledger as a timeout, since that is "
        "what decides whether the executor retries it"
    )
    # The executor's retry policy ran: a timeout is retryable, so the one query
    # was attempted more than once before it was given up on.
    assert len(timed_out) > 1


@covers(Scenario.SEARCH_PROVIDER_TIMEOUT, Scenario.NO_USEFUL_SOURCES)
async def test_a_provider_that_never_answers_ends_the_run_without_a_report(make_world, database):
    """Every search times out, so the run has nothing. It must say so.

    ``synthesis_failed`` is retryable, which is right - a provider outage often
    is not permanent - so the worker spends the run's attempts and then fails it
    for good. What must never happen is a report written from no sources.
    """
    web = story.web()
    web.search_error = httpx.ReadTimeout("the search provider did not answer")
    world = make_world(web=web, brain=story.ordinary_run())

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, *TERMINAL))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.FAILED
    assert row.error["code"] == "synthesis_failed"
    assert row.attempts == world.settings.worker_max_attempts
    assert (row.source_count, row.claim_count) == (0, 0)
    assert world.web.fetches == [], "nothing was searched successfully, so nothing was read"

    async with database.session() as session:
        assert await session.scalar(select(ReportRow).where(ReportRow.run_id == run.id)) is None


# --- the search finds nothing ------------------------------------------------------


@covers(Scenario.EMPTY_SEARCH_RESULTS)
async def test_an_empty_result_list_spends_no_fetch_and_invents_no_source(make_world, database):
    """The provider answers, correctly, with nothing.

    Not an error anywhere: the query ran, the tool call succeeded, and there was
    nothing to choose from. The behaviour under test is that the researcher
    stops there - it does not call the selector, does not fetch, and does not
    turn a subtask with no candidates into a source with no page behind it.
    """
    web = story.web()
    # An answer for a query no researcher will write, so every real query
    # matches nothing and comes back empty.
    web.answers = {"a query nobody writes": [story.PRICE_LIST]}
    world = make_world(web=web, brain=story.ordinary_run())

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, *TERMINAL))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.FAILED
    assert row.error["code"] == "synthesis_failed"
    assert (row.source_count, row.claim_count) == (0, 0)

    assert world.web.searches, "the query was issued"
    assert world.web.fetches == [], "and nothing was fetched on the strength of no results"
    assert world.brain.calls_for(story.SourceSelection) == 0, (
        "a selector asked to choose from an empty catalogue is a model call spent "
        "to learn what the caller already knows"
    )

    search_calls = [
        call for call in world.telemetry.tools.calls if call.tool_name is ToolName.SEARCH
    ]
    assert all(call.status is ToolStatus.OK for call in search_calls), (
        "no results is a successful search, not a failed one"
    )


@covers(Scenario.NO_USEFUL_SOURCES)
async def test_a_page_that_says_nothing_quotable_produces_no_claim_and_no_report(
    make_world, database
):
    """The pages were found and read. Nothing in them could be quoted.

    This is the promise the product is built on, tested from the failing side: a
    quote the extractor cannot find in the passage it named is dropped, so the
    claim that would have rested on it is dropped, so there is nothing to cite
    and no report is written. A system that degraded to uncited prose here would
    look identical to a successful run from the outside.
    """
    brain = story.ordinary_run()
    brain.on(
        EvidenceOutput,
        EvidenceOutput(
            evidence=(
                EvidenceCandidate(
                    passage=1,
                    quote="Provider A charges $9.99 per GPU-hour, according to this page.",
                    stance=EvidenceStance.SUPPORTS,
                ),
            )
        ),
    )
    world = make_world(web=story.web(), brain=brain)

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, *TERMINAL))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.FAILED
    assert row.error["code"] == "synthesis_failed"
    assert row.source_count == 1, "a page was read; it simply did not support anything"
    assert row.claim_count == 0

    async with database.session() as session:
        claims = await session.scalar(
            select(func.count()).select_from(ClaimRow).where(ClaimRow.run_id == run.id)
        )
        assert claims == 0
        assert await session.scalar(select(ReportRow).where(ReportRow.run_id == run.id)) is None


# --- the same page, twice ------------------------------------------------------------


@covers(Scenario.DUPLICATE_URL)
async def test_two_subtasks_that_pick_the_same_page_leave_one_source_behind(make_world, database):
    """Two questions, one answer page, one row.

    Ingestion is idempotent per run and canonical URL, and the graph's reducer
    counts a source once. Both matter and they are not the same thing: without
    the first, two documents would exist with two sets of offsets; without the
    second, the run would report two sources and count the one page twice as
    corroboration.
    """
    brain = story.ordinary_run(
        subtasks=(
            "What does provider A charge per H100 GPU-hour?",
            "What does provider A charge for reserved H100 capacity?",
        )
    )
    world = make_world(web=story.web(), brain=brain, max_subtasks_per_iteration=2)

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, *TERMINAL))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.COMPLETED, row.error
    assert row.source_count == 1, "one page, however many subtasks asked for it"

    async with database.session() as session:
        sources = (
            (await session.execute(select(SourceRow).where(SourceRow.run_id == run.id)))
            .scalars()
            .all()
        )
        documents = (
            (
                await session.execute(
                    select(DocumentRow).where(
                        DocumentRow.source_id.in_([source.id for source in sources])
                    )
                )
            )
            .scalars()
            .all()
        )

    assert [source.url for source in sources] == [story.PRICE_LIST.url]
    assert len(documents) == 1, (
        "a second document for one source would give the same page two sets of "
        "offsets, and a citation could then resolve to either"
    )
    assert world.brain.calls_for(story.SearchQueries) == 2, "both subtasks did run"


@covers(Scenario.DUPLICATE_URL)
async def test_the_same_url_offered_twice_in_one_search_is_fetched_once(make_world, database):
    """A provider that returns a page twice must not cost two fetches.

    Deduplicated before the catalogue is numbered, so a selector cannot spend
    two of its picks on one page either - which is the reason it happens there
    rather than at the fetch.
    """
    web = story.web(story.PRICE_LIST, story.PRICE_LIST, story.MARKET_REVIEW)
    world = make_world(web=web, brain=story.ordinary_run())

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, *TERMINAL))

    assert RunStatus((await read_row(database, run.id)).status) is RunStatus.COMPLETED
    assert world.web.fetches.count(story.PRICE_LIST.url) == 1

    offered = story.offered_results(world.brain.prompts_for(story.SourceSelection)[0])
    assert offered.count(story.PRICE_LIST.url) == 1, "offered once, not twice"
