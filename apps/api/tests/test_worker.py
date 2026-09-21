"""The worker: claiming a run, executing it, and surviving whatever interrupts it.

Every test here drives the real loop against a real database. Only the nine
agents are scripted and only the checkpoint saver is in memory; the queue, the
lease, the sweep, the retry decision and every status transition are the code
that ships. That matters because the interesting failures of this phase are all
races - two workers handed one job, a user cancelling mid-node, a process killed
between two nodes - and none of them is visible without real SQL.

The Postgres checkpointer has its own tests in ``tests/checkpointer``, where a
worker restarts against it too: psycopg needs a selector event loop, which that
directory's conftest provides and this module cannot.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import func, select

from app.agents.nodes import NodeResult
from app.agents.schemas import GraphNode, NodeUsage
from app.core.enums import ResearchMode, RunStatus
from app.db.models.evidence import ClaimRow
from app.db.models.report import ReportRow
from app.db.repositories.worker import SqlAlchemyRunLifecycle
from app.research.repository import utcnow
from app.workers.progress import DISCOVERY_CEILING, NODE_STATUS, progress_for
from tests.support.graph import Script, ScriptedNodes
from tests.support.worker import (
    Harness,
    ago,
    build_worker,
    drained,
    entered,
    read_row,
    respawn,
    seed_run,
    serve_until,
    set_row,
    settled,
    status_is,
    worker_settings,
)

TERMINAL_OR_PAUSED = (
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.PAUSED,
)


@pytest.fixture
def worker_config(settings):
    """Production settings with the worker's timings turned down."""
    return worker_settings(settings)


async def run_one(harness: Harness, database, run_id: uuid.UUID, *until: RunStatus) -> None:
    """Queue a run and let the worker take it to ``until``, then stop the worker.

    The statuses are explicit where a test starts from one of them: a run that is
    already paused satisfies "reached a terminal or paused status" before the
    worker has looked at it, and the test would then assert on nothing.
    """
    await harness.queue.enqueue(run_id)
    await serve_until(harness, settled(harness, database, run_id, *(until or TERMINAL_OR_PAUSED)))


# --- the happy path -------------------------------------------------------------


async def test_a_queued_run_is_executed_and_leaves_a_report_behind(
    database, artifact_store, worker_config
):
    """The claim this build has been unable to make until now: a run that was
    only ever `queued` finishes, and the rows a reader opens are there."""
    run = await seed_run(database, worker_config)
    harness = build_worker(database, artifact_store, worker_config)

    await run_one(harness, database, run.id)

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.COMPLETED
    assert float(row.progress) == 1.0
    assert row.started_at is not None and row.completed_at is not None
    assert row.attempts == 1
    # The lease is given back, so nothing looks held by a process that has
    # finished with it.
    assert (row.worker_id, row.heartbeat_at, row.next_attempt_at) == (None, None, None)
    assert row.langgraph_thread_id == str(run.id)
    assert row.error is None
    assert row.source_count > 0 and row.claim_count > 0

    async with database.session() as session:
        claims = await session.scalar(
            select(func.count()).select_from(ClaimRow).where(ClaimRow.run_id == run.id)
        )
        report = await session.scalar(select(ReportRow).where(ReportRow.run_id == run.id))
    assert claims == row.claim_count
    assert report is not None, "a completed run has a report to serve"


async def test_a_quick_run_finishes_too(database, artifact_store, worker_config):
    """Quick mode has no critic loop, so it leaves the graph by another route."""
    run = await seed_run(database, worker_config, mode=ResearchMode.QUICK)
    harness = build_worker(database, artifact_store, worker_config)

    await run_one(harness, database, run.id)

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.COMPLETED
    assert row.iteration_count == 1


async def test_the_run_row_follows_the_graph_while_it_runs(database, artifact_store, worker_config):
    """Status, progress and counts move at each node boundary rather than only
    at the end: a run that shows nothing for five minutes looks stuck."""
    run = await seed_run(database, worker_config)
    seen: list[tuple[RunStatus, float, int]] = []

    async def snapshot() -> None:
        row = await read_row(database, run.id)
        seen.append((RunStatus(row.status), float(row.progress), row.source_count))

    script = Script(sufficient_on_round=2)
    script.after["critic"] = snapshot
    script.after["synthesizer"] = snapshot
    harness = build_worker(database, artifact_store, worker_config, script=script)

    await run_one(harness, database, run.id)

    statuses = [status for status, _, _ in seen]
    assert RunStatus.QUEUED not in statuses, "the run is not still queued while it runs"
    assert {RunStatus.RESEARCHING, RunStatus.VERIFYING} & set(statuses)
    progresses = [progress for _, progress, _ in seen]
    assert progresses == sorted(progresses), "a progress bar must never go backwards"
    assert all(0.0 < progress <= 1.0 for progress in progresses)
    assert seen[-1][2] > 0, "the source count is live, not written once at the end"


async def test_an_attached_document_is_ingested_before_the_graph_plans(
    client, database, artifact_store, worker_config
):
    """Ingestion is the worker's first act, and the planner is told the corpus
    exists - a document subtask is pointless when there is nothing to search."""
    upload = await client.post(
        "/api/v1/files",
        content=b"# Pricing\n\nInference is billed per token, and per second of GPU time.\n",
        headers={
            "Content-Type": "text/markdown",
            "Content-Disposition": 'attachment; filename="notes.md"',
        },
    )
    assert upload.status_code == 201, upload.text
    created = await client.post(
        "/api/v1/research",
        json={
            "question": "How do AI inference providers price hosted models?",
            "mode": "deep",
            "depth": 3,
            "document_ids": [upload.json()["id"]],
        },
    )
    assert created.status_code == 202, created.text
    run_id = uuid.UUID(created.json()["run_id"])

    harness = build_worker(database, artifact_store, worker_config)
    await run_one(harness, database, run_id)

    assert harness.nodes.plan_states[0]["parameters"].has_attached_documents is True
    row = await read_row(database, run_id)
    assert RunStatus(row.status) is RunStatus.COMPLETED
    # The upload became one of the run's sources, which is what makes it
    # citable in the same way a fetched page is.
    assert row.source_count > 0


# --- idempotency ----------------------------------------------------------------


async def test_a_redelivered_job_for_a_finished_run_is_dropped(
    database, artifact_store, worker_config
):
    """At-least-once delivery is the queue's contract, so the claim has to be
    what stops a second execution - not an assumption that ids arrive once."""
    run = await seed_run(database, worker_config)
    harness = build_worker(database, artifact_store, worker_config)
    await run_one(harness, database, run.id)
    planner_calls = harness.nodes.calls["planner"]

    again = respawn(harness, database, artifact_store)
    await again.queue.enqueue(run.id)
    await serve_until(again, drained(again))

    assert again.nodes.calls["planner"] == planner_calls, "the run was not executed again"
    row = await read_row(database, run.id)
    assert row.attempts == 1, "a dropped job does not spend an attempt"


async def test_two_workers_handed_the_same_job_only_one_claims_it(
    database, artifact_store, worker_config
):
    run = await seed_run(database, worker_config)
    harness = build_worker(database, artifact_store, worker_config)

    first = await harness.lifecycle.claim(run.id, worker_id="worker-a", lease_seconds=60)
    second = await harness.lifecycle.claim(run.id, worker_id="worker-b", lease_seconds=60)

    assert first is not None and first.attempt == 1
    assert second is None
    row = await read_row(database, run.id)
    assert (row.worker_id, row.attempts) == ("worker-a", 1)


# --- failure and retry ----------------------------------------------------------


async def test_a_retryable_failure_pauses_the_run_with_a_backoff(
    database, artifact_store, worker_config
):
    """Paused, not failed: the frontend renders it as resumable, which it is."""
    run = await seed_run(database, worker_config)
    harness = build_worker(
        database, artifact_store, worker_config, script=Script(fail_on={"planner": {1}})
    )

    await run_one(harness, database, run.id)

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.PAUSED
    assert row.attempts == 1
    assert row.error == {
        "code": "planning_failed",
        "message": "The research plan could not be created.",
    }
    # The backoff is the base delay, spread by up to half as much again so that
    # an outage which failed many runs at once does not bring them all back at
    # the same instant.
    base = worker_config.worker_retry_base_delay_seconds
    assert row.next_attempt_at is not None
    delay = (row.next_attempt_at - utcnow()).total_seconds()
    assert base * 0.9 <= delay <= base * 1.5
    assert (row.worker_id, row.heartbeat_at, row.completed_at) == (None, None, None)


async def test_a_run_that_has_used_its_attempts_is_failed_with_the_reason(
    database, artifact_store, worker_config
):
    settings = worker_settings(worker_config, worker_max_attempts=1)
    run = await seed_run(database, settings)
    harness = build_worker(
        database, artifact_store, settings, script=Script(fail_on={"planner": {1}})
    )

    await run_one(harness, database, run.id)

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.FAILED
    assert row.completed_at is not None
    assert row.error is not None and row.error["code"] == "planning_failed"


async def test_a_failure_the_graph_calls_unretryable_is_not_retried(
    database, artifact_store, worker_config
):
    """A node that breaks its contract is a bug. Running it again produces the
    same bug and spends the same money, so the run fails on the first attempt
    even though attempts remain."""
    run = await seed_run(database, worker_config)
    nodes = ScriptedNodes()
    harness = build_worker(
        database,
        artifact_store,
        worker_config,
        nodes=nodes,
        bundle=dataclasses.replace(nodes.bundle(), planner=_BrokenPlanner()),
    )

    await run_one(harness, database, run.id)

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.FAILED
    assert row.attempts == 1, "and only one attempt was spent"
    assert row.error is not None and row.error["code"] == "node_contract_violated"
    # The stored message is the class's own, never the exception's text: an
    # arbitrary message can carry a path, a URL or part of a fetched page.
    assert row.error["message"] == "An internal error stopped the research workflow."


async def test_a_paused_run_resumes_at_the_node_that_failed(
    database, artifact_store, worker_config
):
    """The retry is a resume. A run that failed at synthesis does not research
    everything again - the checkpoint is what the second attempt starts from."""
    run = await seed_run(database, worker_config)
    nodes = ScriptedNodes(Script(fail_on={"synthesizer": {1}}))
    harness = build_worker(database, artifact_store, worker_config, nodes=nodes)

    await run_one(harness, database, run.id)
    assert RunStatus((await read_row(database, run.id)).status) is RunStatus.PAUSED
    researcher_calls = nodes.calls["researcher"]
    assert researcher_calls > 0

    # The backoff has elapsed. Nothing re-queues the run: the sweep is what
    # finds a retry that has come due, which is the point of it.
    await set_row(database, run.id, next_attempt_at=ago(1))
    second = respawn(harness, database, artifact_store)
    await serve_until(second, settled(second, database, run.id, RunStatus.COMPLETED))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.COMPLETED
    assert row.attempts == 2
    assert nodes.calls["researcher"] == researcher_calls, "no research was repeated"
    assert nodes.calls["synthesizer"] == 2


# --- cancellation and lost leases -----------------------------------------------


async def test_a_run_cancelled_while_it_runs_is_not_marked_completed(
    database, artifact_store, worker_config
):
    run = await seed_run(database, worker_config)
    script = Script()

    async def cancel() -> None:
        """What ``ResearchService.cancel`` writes: the status column."""
        await set_row(database, run.id, status=RunStatus.CANCELLED.value, completed_at=utcnow())

    script.after["planner"] = cancel
    harness = build_worker(database, artifact_store, worker_config, script=script)

    await run_one(harness, database, run.id)

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.CANCELLED
    # What the run had already found is still projected. A cancelled run that
    # threw its sources away would have spent the money for nothing.
    assert harness.recorder.states, "the evidence gathered before the cancel was recorded"


async def test_a_worker_whose_lease_was_taken_stops_writing_to_the_run(
    database, artifact_store, worker_config
):
    """Only the worker that holds the run may move it. The first has to notice
    at its next node boundary and let go."""
    run = await seed_run(database, worker_config)
    script = Script()
    harness = build_worker(database, artifact_store, worker_config, script=script)

    async def steal() -> None:
        # lease_seconds=0 makes the current lease expired as of now, which is
        # what a worker that has really died looks like to the next one.
        stolen = await harness.lifecycle.claim(run.id, worker_id="worker-2", lease_seconds=0)
        assert stolen is not None

    script.after["planner"] = steal

    await harness.queue.enqueue(run.id)
    await serve_until(harness, _let_go(harness))

    row = await read_row(database, run.id)
    assert row.worker_id == "worker-2", "the first worker did not overwrite the second"
    assert RunStatus(row.status) is not RunStatus.COMPLETED
    assert row.completed_at is None


def _let_go(harness: Harness) -> Callable[[], Awaitable[bool]]:
    async def condition() -> bool:
        return harness.nodes.calls["planner"] >= 1 and not harness.worker.busy

    return condition


# --- reconciliation -------------------------------------------------------------


async def test_the_sweep_redispatches_what_the_queue_lost(database, artifact_store, worker_config):
    """Three populations, three ways a run can fall off the queue: a job that
    never arrived, a retry that came due, and a worker that died."""
    settings = worker_settings(worker_config, worker_queued_grace_seconds=5.0)
    harness = build_worker(database, artifact_store, settings)

    forgotten = await seed_run(database, settings, created_at=ago(60))
    due = await seed_run(database, settings)
    await set_row(database, due.id, status=RunStatus.PAUSED.value, next_attempt_at=ago(5))
    abandoned = await seed_run(database, settings)
    await set_row(
        database,
        abandoned.id,
        status=RunStatus.RESEARCHING.value,
        worker_id="a-worker-that-died",
        heartbeat_at=ago(3600),
    )
    # Not due: created a moment ago, so its own dispatch is still in flight.
    fresh = await seed_run(database, settings)

    dispatched = await harness.worker.reconcile()

    assert dispatched == 3
    assert set(harness.queue.jobs) == {forgotten.id, due.id, abandoned.id}
    assert fresh.id not in harness.queue.jobs


async def test_the_sweep_does_not_re_dispatch_a_backlog_it_already_dispatched(
    database, artifact_store, worker_config
):
    """The defect Phase 21's load test measured: 4,662 queue entries for 100 jobs.

    A run that is still ``queued`` because every worker slot is busy answers
    "I should be on the queue" on every sweep, and neither the sweep nor the
    queue deduplicates - so the backlog was re-enqueued in full, every sweep,
    for as long as it lasted. Nothing ran twice, because claiming is a
    conditional update; what broke was ``queue_depth``, which is the gauge on
    the dashboard and the obvious input to an autoscaler.

    Driven through ``lifecycle.due`` rather than the loop, because that is
    where the rule lives and the loop would only add its sweep interval.
    """
    settings = worker_settings(worker_config, worker_queued_grace_seconds=5.0)
    lifecycle = SqlAlchemyRunLifecycle(database)
    backlog = {(await seed_run(database, settings, created_at=ago(60))).id for _ in range(3)}

    first = await lifecycle.due(lease_seconds=60, queued_grace_seconds=5.0, limit=100)
    second = await lifecycle.due(lease_seconds=60, queued_grace_seconds=5.0, limit=100)

    assert set(first) == backlog
    assert second == []


async def test_a_dispatch_old_enough_to_be_presumed_lost_is_repeated(
    database, artifact_store, worker_config
):
    """The sweep is a repair mechanism, and it must still repair.

    Suppressing a re-dispatch forever would turn one lost queue message into a
    run that waits for a person to notice.
    """
    lifecycle = SqlAlchemyRunLifecycle(database)
    run = await seed_run(database, worker_config, created_at=ago(60))
    await set_row(database, run.id, last_queued_at=ago(60))

    due = await lifecycle.due(lease_seconds=60, queued_grace_seconds=5.0, limit=100)

    assert due == [run.id]


async def test_a_run_handed_back_since_its_dispatch_goes_out_again_at_once(
    database, artifact_store, worker_config
):
    """ "Something has happened to it since" is what keeps a retry from waiting.

    A worker that pauses a run writes ``updated_at``, which is the whole of the
    rule: the dispatch that led to that attempt is spent, so the next one does
    not queue behind a grace period the user would feel as a stall. The grace
    here is five minutes, so a rule that waited for it would fail this.
    """
    lifecycle = SqlAlchemyRunLifecycle(database)
    run = await seed_run(database, worker_config, created_at=ago(600))

    assert await lifecycle.due(lease_seconds=60, queued_grace_seconds=300.0, limit=100) == [run.id]

    # A worker took it, failed, and handed it back for a retry that is due.
    await lifecycle.claim(run.id, worker_id="worker-1", lease_seconds=60)
    await lifecycle.release(run.id, worker_id="worker-1", retry_in_seconds=0)

    assert await lifecycle.due(lease_seconds=60, queued_grace_seconds=300.0, limit=100) == [run.id]


async def test_dispatching_a_run_does_not_count_as_changing_it(
    database, artifact_store, worker_config
):
    """``updated_at`` must not move when the sweep stamps a row.

    ``UpdatedAtMixin`` declares an application-side ``onupdate`` and SQLAlchemy
    applies it to a Core UPDATE too, so the stamp pins the column to itself. If
    that pin is ever dropped, ``last_queued_at`` and ``updated_at`` become equal
    and the rule above is satisfied on the very next sweep - the suppression
    would silently become a no-op while every other test still passed.
    """
    lifecycle = SqlAlchemyRunLifecycle(database)
    run = await seed_run(database, worker_config, created_at=ago(60))
    before = (await read_row(database, run.id)).updated_at

    await lifecycle.due(lease_seconds=60, queued_grace_seconds=5.0, limit=100)

    row = await read_row(database, run.id)
    assert row.updated_at == before
    assert row.last_queued_at is not None and row.last_queued_at > before


async def test_a_run_whose_worker_died_is_claimed_by_the_next_one(
    database, artifact_store, worker_config
):
    """The lease, end to end: a run left mid-flight by a process that is gone is
    picked up, and the attempt that process spent is not given back."""
    run = await seed_run(database, worker_config)
    await set_row(
        database,
        run.id,
        status=RunStatus.RESEARCHING.value,
        worker_id="a-worker-that-died",
        heartbeat_at=ago(3600),
        started_at=ago(3600),
        attempts=1,
    )
    harness = build_worker(database, artifact_store, worker_config, worker_id="worker-2")

    await run_one(harness, database, run.id, RunStatus.COMPLETED)

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.COMPLETED
    assert row.attempts == 2, "the dead worker's attempt still counts"
    assert row.started_at < row.completed_at


# --- shutdown -------------------------------------------------------------------


async def test_shutting_down_hands_the_run_back_without_spending_an_attempt(
    database, artifact_store, worker_config
):
    """A deploy is not a failure. The run comes back as paused, is re-queued,
    and the attempt the stopping worker claimed is returned - otherwise three
    deploys in a row would exhaust a run that never went wrong."""
    run = await seed_run(database, worker_config)
    harness = build_worker(
        database, artifact_store, worker_config, script=Script(hang={"researcher"})
    )

    await harness.queue.enqueue(run.id)
    # Stopped while a researcher is genuinely mid-call, which is the case the
    # grace period and the cancellation exist for.
    await serve_until(harness, entered(harness, "researcher"))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.PAUSED
    assert row.attempts == 0, "the attempt was refunded"
    assert (row.worker_id, row.heartbeat_at, row.next_attempt_at) == (None, None, None)
    assert row.completed_at is None
    assert harness.queue.jobs == [run.id], "and it is back on the queue"


async def test_a_handed_back_run_is_finished_by_the_next_worker(
    database, artifact_store, worker_config
):
    """The restart guarantee, stated the way the product states it: research
    survives a worker restart rather than starting over."""
    run = await seed_run(database, worker_config)
    first = build_worker(
        database,
        artifact_store,
        worker_config,
        script=Script(hang={"critic"}),
        worker_id="worker-1",
    )
    await first.queue.enqueue(run.id)
    await serve_until(first, status_is(database, run.id, RunStatus.VERIFYING))
    assert RunStatus((await read_row(database, run.id)).status) is RunStatus.PAUSED
    assert first.nodes.calls["researcher"] > 0

    second = respawn(first, database, artifact_store, nodes=ScriptedNodes(Script()))
    await run_one(second, database, run.id, RunStatus.COMPLETED)

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.COMPLETED
    assert second.nodes.calls["planner"] == 0, "the plan was restored, not remade"
    assert second.nodes.calls["researcher"] == 0, "and no research was repeated"


# --- concurrency ----------------------------------------------------------------


async def test_one_worker_starts_no_more_runs_than_its_concurrency_allows(
    database, artifact_store, worker_config
):
    """The bound is the connection pool and the gateway's semaphore, which are
    per process - so a worker that ignored it would degrade every run it holds."""
    settings = worker_settings(worker_config, worker_concurrency=1)
    first = await seed_run(database, settings)
    second = await seed_run(database, settings)
    harness = build_worker(database, artifact_store, settings, script=Script(hang={"researcher"}))

    await harness.queue.enqueue(first.id)
    await harness.queue.enqueue(second.id)
    await serve_until(harness, entered(harness, "researcher"))

    row = await read_row(database, second.id)
    assert row.attempts == 0, "the second run was never claimed while the slot was taken"
    assert row.worker_id is None
    assert second.id in harness.queue.jobs, "its job is still waiting, not lost"


async def test_a_worker_with_two_slots_runs_two_researches_at_once(
    database, artifact_store, worker_config
):
    settings = worker_settings(worker_config, worker_concurrency=2)
    first = await seed_run(database, settings)
    second = await seed_run(database, settings)
    harness = build_worker(database, artifact_store, settings, script=Script(hang={"researcher"}))

    await harness.queue.enqueue(first.id)
    await harness.queue.enqueue(second.id)
    await serve_until(harness, _both_claimed(harness, database, first.id, second.id))

    for run in (first, second):
        row = await read_row(database, run.id)
        assert RunStatus(row.status) is RunStatus.PAUSED, "both were handed back on the way out"
        assert row.attempts == 0


def _both_claimed(
    harness: Harness, database, first_id: uuid.UUID, second_id: uuid.UUID
) -> Callable[[], Awaitable[bool]]:
    """Both runs held by this worker at the same moment - which a worker with
    one slot could not do, and is the whole of what concurrency buys."""

    async def condition() -> bool:
        rows = [await read_row(database, run_id) for run_id in (first_id, second_id)]
        return all(row.worker_id == harness.worker_id for row in rows)

    return condition


# --- the progress function ------------------------------------------------------


def test_every_node_has_a_status_and_a_place_on_the_bar():
    """A node missing from either table raises a KeyError at a node boundary,
    which would end a research run that had otherwise succeeded."""
    assert set(NODE_STATUS) == set(GraphNode)
    for node in GraphNode:
        assert 0.0 < progress_for(node, iteration=1, max_iterations=4) <= 1.0


def test_every_node_is_attributed_to_an_agent_in_the_ledger():
    """A node missing from ``NODE_AGENT`` leaves no trace of itself at all.

    Worse than the table above, because it fails *quietly*: the KeyError is
    raised inside the ledger's guarded write, so the step runs, succeeds, and
    writes neither its span nor the model call that hung from it. The answerer
    shipped that way until a scenario counted the ledger against the calls the
    run had actually made.
    """
    from app.observability.ledger import NODE_AGENT

    assert set(NODE_AGENT) == set(GraphNode)


def test_progress_never_goes_backwards_across_a_four_round_run():
    discovery = [
        GraphNode.PLANNER,
        GraphNode.RESEARCHER,
        GraphNode.EVIDENCE_EXTRACTOR,
        GraphNode.CLAIM_NORMALIZER,
        GraphNode.VERIFIER,
        GraphNode.CONTRADICTION_CHECKER,
        GraphNode.CRITIC,
    ]
    trace = [
        progress_for(node, iteration=iteration, max_iterations=4)
        for iteration in (1, 2, 3, 4)
        for node in discovery
    ]
    trace += [
        progress_for(GraphNode.SYNTHESIZER, iteration=4, max_iterations=4),
        progress_for(GraphNode.CITATION_VALIDATOR, iteration=4, max_iterations=4),
    ]

    assert trace == sorted(trace)
    assert trace[0] > 0.0, "a run that has planned has visibly started"
    beyond = max(progress_for(node, iteration=9, max_iterations=4) for node in discovery)
    assert beyond == DISCOVERY_CEILING, "a round past the ceiling is clamped, not pushed past it"


class _BrokenPlanner:
    """A node that returns something its Protocol does not promise."""

    async def plan(self, state) -> NodeResult:
        return NodeResult("not a plan", NodeUsage())
