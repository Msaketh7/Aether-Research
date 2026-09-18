"""Scenarios 5, 14 and 15: the run outlives the process, or the user ends it.

The three situations where the state that matters is not in memory. A worker is
restarted mid-run by a deploy; a user presses cancel while a node is in flight;
a process dies and another picks the run up. Each is a race between a graph and
a row, and none of them is visible without real SQL and two real processes.

**Each is entered while a node is genuinely running**, because none of these
things waits for a node boundary. A deploy or a lease takeover is a call that
never returns and a worker that is then stopped; a cancellation is a model call
that writes the user's decision as a side effect and then answers normally, so
the graph's probe meets it at the next boundary exactly as it would in
production. Stopping a worker *between* nodes would prove nothing about either.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Awaitable, Callable

from pydantic import BaseModel
from sqlalchemy import func, select

from app.agents.outputs import CritiqueOutput, EvidenceOutput, SearchQueries
from app.core.enums import ResearchEventType, RunStatus
from app.db.models.event import ResearchEventRow
from app.db.models.report import ReportRow
from app.db.models.source import SourceRow
from app.models.base import CompletionRequest
from app.research.events import EventDraft
from app.research.repository import utcnow
from tests.scenarios import story
from tests.scenarios.catalogue import Scenario, covers
from tests.scenarios.world import (
    SCENARIO_DEADLINE_SECONDS,
    World,
    queue_run,
    respawned,
    run_until,
    scripted_dns,
)
from tests.support.worker import read_row, serve_until, set_row, settled


def asked(world: World, schema: type, times: int = 1) -> Callable[[], Awaitable[bool]]:
    """True once the scripted model has been asked for ``schema`` this often.

    The scenario suite's equivalent of the scripted-node call counter: it is how
    a test stops a worker while a run is inside a node rather than waiting for a
    timeout it would then be measuring instead.
    """

    async def condition() -> bool:
        return world.brain.calls_for(schema) >= times

    return condition


async def events_of(database, run_id, kind: ResearchEventType) -> int:
    async with database.session() as session:
        return await session.scalar(
            select(func.count())
            .select_from(ResearchEventRow)
            .where(ResearchEventRow.run_id == run_id, ResearchEventRow.type == kind.value)
        )


# --- a user changes their mind --------------------------------------------------------


def cancels_the_run(database, run_id, then):
    """A rule that writes the user's cancellation, then answers normally.

    The cancel lands while a node is genuinely running, which is the case that
    matters: a run cancelled between nodes would be stopped by the queue, and a
    run cancelled during one is stopped by the graph's probe at the next
    boundary. Only the status column is written, because that is all
    ``ResearchService.cancel`` writes.
    """

    async def rule(request: CompletionRequest) -> BaseModel:
        await set_row(database, run_id, status=RunStatus.CANCELLED.value, completed_at=utcnow())
        answer = then(request)
        return await answer if asyncio.iscoroutine(answer) else answer

    return rule


@covers(Scenario.USER_CANCELLATION)
async def test_a_run_cancelled_mid_node_stops_and_leaves_no_report(make_world, database):
    """Cancelled while the extractor is in flight, as a user would.

    The row is written from outside, and the graph's probe is what notices. What
    must not happen is the worker finishing the run anyway: the report would be
    written after the user asked for the run to stop, and the completed status
    would overwrite a decision this worker did not make.
    """
    brain = story.ordinary_run()
    world = make_world(web=story.web(), brain=brain)

    with scripted_dns():
        run = await queue_run(world)
        brain.on(
            EvidenceOutput,
            cancels_the_run(database, run.id, story.quoting(*story.QUOTES.values())),
        )
        await run_until(world, settled(world.harness, database, run.id, RunStatus.CANCELLED))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.CANCELLED, "the user's decision stands"
    assert brain.calls_for(story.ReportOutput) == 0, "no report was written after the cancel"

    async with database.session() as session:
        assert await session.scalar(select(ReportRow).where(ReportRow.run_id == run.id)) is None


@covers(Scenario.USER_CANCELLATION)
async def test_a_cancelled_run_keeps_what_it_gathered_and_is_announced_once(make_world, database):
    """Cancelling is not a rollback, and it is not announced twice.

    The sources the run had already read stay: they were fetched, stored and
    paid for, and a user who cancels half way through is entitled to see what it
    found. The API publishes the cancellation when the user asks for it, so the
    worker observing the run stop must not announce it again - a client would
    render the run as ending twice.
    """
    brain = story.ordinary_run()
    world = make_world(web=story.web(), brain=brain)

    with scripted_dns():
        run = await queue_run(world)
        brain.on(
            CritiqueOutput,
            cancels_the_run(database, run.id, lambda _r: story.critique(sufficient=True)),
        )
        # What the API does on POST /research/{id}/cancel, after the row: the
        # event, so a watching client hears it at once rather than at the
        # worker's next boundary.
        await world.harness.broker.publish(
            EventDraft(
                type=ResearchEventType.RESEARCH_CANCELLED,
                run_id=run.id,
                status=RunStatus.CANCELLED,
                payload={"cancelled_by": "user"},
            )
        )
        await run_until(world, settled(world.harness, database, run.id, RunStatus.CANCELLED))

    assert RunStatus((await read_row(database, run.id)).status) is RunStatus.CANCELLED

    async with database.session() as session:
        sources = await session.scalar(
            select(func.count()).select_from(SourceRow).where(SourceRow.run_id == run.id)
        )
    assert sources == 1, "a page it had already read is still a page it read"
    assert await events_of(database, run.id, ResearchEventType.RESEARCH_CANCELLED) == 1


# --- the process goes away ------------------------------------------------------------


@covers(Scenario.WORKER_RESTART)
async def test_a_deploy_mid_run_hands_the_run_back_rather_than_failing_it(make_world, database):
    """A restart is not a failure, and must not cost the run an attempt.

    Three deploys in a row would otherwise exhaust a run that never went wrong.
    The stopping worker returns the attempt it claimed, clears the lease and
    re-queues the run, which is what makes "research survives a worker restart"
    a property rather than a hope.
    """
    brain = story.ordinary_run()
    brain.hangs(SearchQueries)
    world = make_world(web=story.web(), brain=brain)

    with scripted_dns():
        run = await queue_run(world)
        await serve_until(
            world.harness, asked(world, SearchQueries), deadline_seconds=SCENARIO_DEADLINE_SECONDS
        )

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.PAUSED
    assert row.attempts == 0, "the attempt was refunded"
    assert (row.worker_id, row.heartbeat_at, row.next_attempt_at) == (None, None, None)
    assert world.harness.queue.jobs == [run.id], "and it is back on the queue"


@covers(Scenario.WORKER_RESTART, Scenario.RESUME_AFTER_INTERRUPTION)
async def test_the_next_worker_finishes_the_run_without_repeating_the_research(
    make_world, database
):
    """The restart guarantee, stated as the product states it.

    A second process takes the handed-back run and carries on from the
    checkpoint: it does not plan again, and it does not fetch again. That last
    one is the expensive half - re-research would double the cost of every
    deploy - and it is only observable from outside the graph, by watching what
    the scripted web was asked for.
    """
    brain = story.ordinary_run()
    brain.hangs(CritiqueOutput)
    first = make_world(web=story.web(), brain=brain)

    with scripted_dns():
        run = await queue_run(first)
        await serve_until(
            first.harness, asked(first, CritiqueOutput), deadline_seconds=SCENARIO_DEADLINE_SECONDS
        )
        assert RunStatus((await read_row(database, run.id)).status) is RunStatus.PAUSED
        fetched = list(first.web.fetches)
        assert fetched, "the first process did do some research"

        # A new process over the same queue, the same database and the same
        # checkpoints - and a model that now answers the call that hung.
        brain.on(CritiqueOutput, story.critique(sufficient=True))
        second = respawned(first)
        planned_before = brain.calls_for(story.PlanOutput)
        await run_until(second, settled(second.harness, database, run.id, RunStatus.COMPLETED))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.COMPLETED, row.error
    assert row.attempts == 1, "the second process spent the first attempt, not a second one"
    assert brain.calls_for(story.PlanOutput) == planned_before, "the plan was restored, not remade"
    assert first.web.fetches == fetched, "and no page was fetched twice"

    async with database.session() as session:
        reports = await session.scalar(
            select(func.count()).select_from(ReportRow).where(ReportRow.run_id == run.id)
        )
    assert reports == 1, "one run, one report, however many processes it took"


@covers(Scenario.RESUME_AFTER_INTERRUPTION)
async def test_a_run_whose_worker_died_is_reclaimed_and_finished(make_world, database):
    """Not a deploy: a process that stopped without handing anything back.

    The row still says `researching` and still names a worker, so nothing will
    re-queue it. The lease is what expires, and the next worker's sweep is what
    notices - which is the only path by which a killed container's work is ever
    finished.
    """
    brain = story.ordinary_run()
    brain.hangs(CritiqueOutput)
    first = make_world(web=story.web(), brain=brain)

    with scripted_dns():
        run = await queue_run(first)
        await serve_until(
            first.harness, asked(first, CritiqueOutput), deadline_seconds=SCENARIO_DEADLINE_SECONDS
        )

        # Undo the graceful hand-back, so the row looks as a killed process left
        # it: held, mid-run, and with a heartbeat that stopped ten minutes ago.
        # Nothing re-queues a row in that state - the sweep is what finds it,
        # which is the whole reason the sweep exists.
        stale = utcnow() - dt.timedelta(seconds=600)
        await set_row(
            database,
            run.id,
            status=RunStatus.RESEARCHING.value,
            worker_id="worker-that-died",
            heartbeat_at=stale,
            next_attempt_at=None,
        )
        first.harness.queue.jobs.clear()

        brain.on(CritiqueOutput, story.critique(sufficient=True))
        second = respawned(first)
        await run_until(
            second,
            settled(second.harness, database, run.id, RunStatus.COMPLETED),
            deadline_seconds=SCENARIO_DEADLINE_SECONDS,
        )

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.COMPLETED, row.error
    assert row.worker_id is None

    async with database.session() as session:
        assert await session.scalar(select(ReportRow).where(ReportRow.run_id == run.id)) is not None


@covers(Scenario.RESUME_AFTER_INTERRUPTION)
async def test_a_resumed_run_continues_its_event_stream_rather_than_restarting_it(
    make_world, database
):
    """What a client watching the run sees across the interruption.

    The second process has its own transport and inherits nothing but the
    durable log, so continuing rather than restarting is a property of the log,
    not of the process. A stream that repeated its earlier events would make a
    reconnecting client render the run twice.
    """
    brain = story.ordinary_run()
    brain.hangs(CritiqueOutput)
    first = make_world(web=story.web(), brain=brain)

    with scripted_dns():
        run = await queue_run(first)
        await serve_until(
            first.harness, asked(first, CritiqueOutput), deadline_seconds=SCENARIO_DEADLINE_SECONDS
        )
        brain.on(CritiqueOutput, story.critique(sufficient=True))
        second = respawned(first)
        await run_until(second, settled(second.harness, database, run.id, RunStatus.COMPLETED))

    async with database.session() as session:
        events = (
            (
                await session.execute(
                    select(ResearchEventRow)
                    .where(ResearchEventRow.run_id == run.id)
                    .order_by(ResearchEventRow.seq)
                )
            )
            .scalars()
            .all()
        )

    sequences = [event.seq for event in events]
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences), (
        "one numbering across two processes: `publish` numbers an event by storing it, "
        "so two emitters can never share an id"
    )
    assert await events_of(database, run.id, ResearchEventType.REPORT_COMPLETED) == 1
    started = [event for event in events if event.type == ResearchEventType.RESEARCH_STARTED.value]
    assert len(started) <= 1, "a resumed run is not announced as a new one"
