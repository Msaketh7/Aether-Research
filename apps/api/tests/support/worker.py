"""Assembling a worker for the tests, over real Postgres and scripted agents.

Everything here is the production object except the nine agents, which are the
graph tests' scripted stand-ins, and the checkpoint saver, which is in memory.
The queue is the in-memory adapter the test environment already selects, and the
lease, the sweep and every status transition run as real SQL against the real
table - which is the half of this phase that could not be proved any other way.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select, update

from app.agents.checkpoint import build_serializer
from app.agents.graph import GraphBounds
from app.agents.nodes import ResearchNodes
from app.agents.runtime import ResearchGraphRunner
from app.agents.schemas import SourceRef
from app.core.config import Settings
from app.core.enums import ResearchMode, RunStatus, SourceType
from app.core.ids import new_id
from app.db.models.research import ResearchRunRow
from app.db.models.source import DocumentRow, SourceRow
from app.db.repositories.research import SqlAlchemyResearchRepository
from app.db.repositories.trace import SqlAlchemyTraceStore
from app.db.repositories.user import UserRepository
from app.db.repositories.worker import SqlAlchemyRunLifecycle
from app.db.session import Database
from app.models.budget import RunBudgetGuard
from app.observability.ledger import DatabaseTracer
from app.research.cancellation import PostgresCancellationProbe
from app.research.eventbus import build_event_broker
from app.research.events import EventBroker
from app.research.recorder import RunRecorder
from app.research.repository import utcnow
from app.research.schemas import ResearchRun, RunLimits, RunUsage, derive_title
from app.retrieval.attached import AttachedUploadIngestion
from app.storage import ObjectStorage
from app.workers.events import StoredReportFacts
from app.workers.loop import ResearchWorker
from app.workers.queue import InMemoryJobQueue
from app.workers.worker import RunExecutor
from tests.support.graph import RecordedRuns, Script, ScriptedNodes, stable_id
from tests.support.ingestion import in_process_ingestor

#: Timings that make a loop test finish in milliseconds rather than minutes.
#: Every one of them is a production setting turned down, not a code path.
FAST_TIMINGS: dict[str, object] = {
    "worker_poll_seconds": 0.05,
    "worker_sweep_interval_seconds": 0.05,
    "worker_queued_grace_seconds": 30.0,
    "worker_shutdown_grace_seconds": 0.2,
    "worker_retry_base_delay_seconds": 30.0,
    "worker_retry_max_delay_seconds": 60.0,
    "graph_node_timeout_seconds": 5.0,
    "worker_lease_seconds": 60,
}


def worker_settings(base: Settings, **overrides: object) -> Settings:
    """``base`` with the worker turned down to test speed, plus any overrides."""
    return Settings(**{**base.model_dump(), **FAST_TIMINGS, **overrides})


async def seed_user(database: Database, user_id: uuid.UUID) -> None:
    async with database.session() as session:
        await UserRepository(session).ensure(user_id, f"{user_id}@example.com")


async def seed_run(
    database: Database,
    settings: Settings,
    *,
    user_id: uuid.UUID | None = None,
    mode: ResearchMode = ResearchMode.DEEP,
    status: RunStatus = RunStatus.QUEUED,
    question: str = "How do AI inference providers price hosted models?",
    **row_values: object,
) -> ResearchRun:
    """A run row shaped exactly as ``ResearchService.create`` writes one.

    Through the repository rather than by hand, because the worker reads the
    frozen ``limits`` back and a row with an empty one would fail to map - which
    is a real failure mode, and not the one a worker test is about.
    """
    owner = user_id or uuid.uuid4()
    await seed_user(database, owner)
    now = utcnow()
    run = ResearchRun(
        id=new_id(),
        user_id=owner,
        parent_run_id=None,
        title=derive_title(question),
        question=question,
        mode=mode,
        depth=1 if mode is ResearchMode.QUICK else 3,
        domains=[],
        date_range_start=None,
        date_range_end=None,
        status=status,
        progress=0.0,
        limits=RunLimits.for_mode(mode, settings),
        usage=RunUsage(),
        source_count=0,
        claim_count=0,
        contradiction_count=0,
        coverage_caveat=None,
        has_report=False,
        created_at=now,
        started_at=None,
        completed_at=None,
        error=None,
    )
    async with database.session() as session:
        stored = await SqlAlchemyResearchRepository(session).add(run)
    if row_values:
        await set_row(database, stored.id, **row_values)
    return stored


async def set_row(database: Database, run_id: uuid.UUID, **values: object) -> None:
    """Write columns the repository does not expose - a lease, an older clock."""
    async with database.session() as session:
        await session.execute(
            update(ResearchRunRow).where(ResearchRunRow.id == run_id).values(**values)
        )


async def read_row(database: Database, run_id: uuid.UUID) -> ResearchRunRow:
    async with database.session() as session:
        row = (
            await session.execute(select(ResearchRunRow).where(ResearchRunRow.id == run_id))
        ).scalar_one()
        session.expunge(row)
    return row


@dataclass
class Harness:
    """One worker process, and the handles a test needs on its parts.

    A worker that has been stopped stays stopped - that is what a process does -
    so a test covering a restart builds a second harness with ``respawn``,
    sharing the queue, the scripted agents and the checkpoint saver the way two
    processes share Redis, an image and Postgres.
    """

    worker: ResearchWorker
    queue: InMemoryJobQueue
    lifecycle: SqlAlchemyRunLifecycle
    nodes: ScriptedNodes
    recorder: RecordedRuns
    checkpointer: BaseCheckpointSaver[Any]
    #: The real broker the worker publishes to: an in-memory transport over
    #: the real durable log, which is what ``APP_ENV=test`` selects.
    broker: EventBroker
    settings: Settings
    worker_id: str


def build_worker(
    database: Database,
    storage: ObjectStorage,
    settings: Settings,
    *,
    script: Script | None = None,
    nodes: ScriptedNodes | None = None,
    bundle: ResearchNodes | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    queue: InMemoryJobQueue | None = None,
    worker_id: str = "worker-1",
    budget: RunBudgetGuard | None = None,
) -> Harness:
    scripted = nodes or ScriptedNodes(script or Script())
    job_queue = queue or InMemoryJobQueue()
    broker = build_event_broker(settings, database=database)
    saver = checkpointer or InMemorySaver(serde=build_serializer())
    lifecycle = SqlAlchemyRunLifecycle(database)
    recorder = RecordedRuns()
    nodes_bundle = bundle
    if nodes_bundle is None:
        # Only the scripted researcher needs its rows seeded. A real one writes
        # them itself, and wrapping it would insert nothing and hide nothing -
        # but it would also make the scenario suite (Phase 19) depend on a
        # helper that exists for the opposite kind of test.
        nodes_bundle = dataclasses.replace(
            scripted.bundle(), researcher=_IngestingResearcher(scripted, database)
        )
    runner = ResearchGraphRunner(
        nodes=nodes_bundle,
        checkpointer=saver,
        probe=PostgresCancellationProbe(database),
        bounds=GraphBounds.from_settings(settings),
        recorder=_Both(RunRecorder(database), recorder),
        # The real tracer, as `app.workers.runner` wires it: the trace is a
        # deliverable of the run, so a harness that left it out would test a
        # worker no deployment runs (Phase 16).
        tracer=DatabaseTracer(SqlAlchemyTraceStore(database)),
    )
    worker = ResearchWorker(
        queue=job_queue,
        lifecycle=lifecycle,
        executor=RunExecutor(
            lifecycle=lifecycle,
            runner=runner,
            uploads=AttachedUploadIngestion(
                database=database,
                storage=storage,
                ingestor=in_process_ingestor(database, storage),
            ),
            broker=broker,
            reports=StoredReportFacts(database),
            settings=settings,
            # The guard the gateway was built with, so a run's ceiling is
            # enforced before each call rather than only between nodes. A
            # scripted-node harness has no gateway and passes none (Phase 16).
            budget=budget,
        ),
        settings=settings,
        worker_id=worker_id,
    )
    return Harness(
        worker=worker,
        queue=job_queue,
        lifecycle=lifecycle,
        nodes=scripted,
        recorder=recorder,
        checkpointer=saver,
        broker=broker,
        settings=settings,
        worker_id=worker_id,
    )


def respawn(
    harness: Harness,
    database: Database,
    storage: ObjectStorage,
    *,
    nodes: ScriptedNodes | None = None,
    bundle: ResearchNodes | None = None,
    worker_id: str = "worker-2",
    settings: Settings | None = None,
    budget: RunBudgetGuard | None = None,
) -> Harness:
    """The next worker process: same queue, same checkpoints, new loop.

    A *new* broker, deliberately: a replacement process has its own transport
    and inherits nothing but the durable log, which is exactly what has to be
    enough for the resumed run's stream to continue rather than restart.
    """
    return build_worker(
        database,
        storage,
        settings or harness.settings,
        nodes=nodes or harness.nodes,
        bundle=bundle,
        checkpointer=harness.checkpointer,
        queue=harness.queue,
        worker_id=worker_id,
        budget=budget,
    )


class _Both:
    """Projects for real, and keeps a copy for the test to assert on."""

    def __init__(self, real: RunRecorder, watcher: RecordedRuns) -> None:
        self._real = real
        self._watcher = watcher

    async def record(self, state: Any) -> object:
        await self._watcher.record(state)
        return await self._real.record(state)


class _IngestingResearcher:
    """Writes the rows a real researcher would have written before reporting.

    A production researcher fetches a page, ingests it, and reports the id of the
    source row it has just stored. The scripted one invents ids, and the evidence
    projection's foreign keys are what notice the difference - ``evidence`` has
    one to ``documents``. So the harness stores the pair each reported source
    stands for, which is the smallest thing that makes the scripted run as real
    as the projection needs it to be.
    """

    def __init__(self, inner: Any, database: Database) -> None:
        self._inner = inner
        self._database = database

    async def research(self, assignment: Any) -> Any:
        result = await self._inner.research(assignment)
        for source in result.value.sources:
            await seed_source(self._database, assignment.research_id, source)
        return result


async def seed_source(database: Database, run_id: uuid.UUID, source: SourceRef) -> None:
    """A source row and its document, at the ids the scripted graph will cite."""
    passage = f"A finding from {source.title}"
    async with database.session() as session:
        if await session.get(SourceRow, source.source_id) is not None:
            return  # a resumed run reports the sources it already stored
        session.add(
            SourceRow(
                id=source.source_id,
                run_id=run_id,
                url=source.url,
                canonical_url=source.url,
                domain="example.test",
                source_type=SourceType.WEB.value,
                title=source.title,
                publisher="example.test",
                accessed_at=utcnow(),
                # Distinct per source, so deduplication does not collapse the
                # scripted corpus into one cluster.
                content_hash=source.source_id.hex,
                credibility_score=0.5,
                credibility_metadata={},
                excerpt=passage,
            )
        )
        session.add(
            DocumentRow(
                id=stable_id("document", source.source_id),
                source_id=source.source_id,
                normalized_content=passage,
                content_hash=source.source_id.hex,
            )
        )


async def serve_until(
    harness: Harness,
    condition: Callable[[], Awaitable[bool]],
    *,
    #: Sixty rather than twenty since Phase 19: the scenario suite made the
    #: parallel run heavier, and a worker loop that is simply starved of CPU
    #: was reaching this. It is a hang detector, not a measurement - nothing
    #: here is asserting how fast a run is.
    deadline_seconds: float = 60.0,
) -> None:
    """Run the real loop until ``condition`` holds, then stop it and drain.

    The loop is the thing under test, so the tests drive it as a process does -
    start it, wait for the database to say what they are waiting for, signal it
    to stop - rather than calling its steps by hand.
    """
    task = asyncio.create_task(harness.worker.serve())
    try:
        async with asyncio.timeout(deadline_seconds):
            while not await condition():
                if task.done():
                    await task  # surfaces whatever ended the loop
                    raise AssertionError("the worker stopped before the condition held")
                await asyncio.sleep(0.02)
    finally:
        harness.worker.stop()
        await asyncio.wait_for(task, timeout=deadline_seconds)


def status_is(
    database: Database, run_id: uuid.UUID, *statuses: RunStatus
) -> Callable[[], Awaitable[bool]]:
    """True once the run reaches one of ``statuses``."""

    async def condition() -> bool:
        row = await read_row(database, run_id)
        return RunStatus(row.status) in statuses

    return condition


def drained(harness: Harness) -> Callable[[], Awaitable[bool]]:
    """True once the worker has taken every queued job and finished with it.

    The condition for a job that produces no state change - a redelivery of a
    run that is already finished. Waiting on the run's status instead would pass
    before the worker had even looked at it.
    """

    async def condition() -> bool:
        return not harness.queue.jobs and not harness.worker.busy

    return condition


def entered(harness: Harness, node: str, times: int = 1) -> Callable[[], Awaitable[bool]]:
    """True once a scripted node has been called, so a test can stop a worker
    while a run is genuinely inside one rather than waiting for it to time out."""

    async def condition() -> bool:
        return harness.nodes.calls[node] >= times

    return condition


def settled(
    harness: Harness, database: Database, run_id: uuid.UUID, *statuses: RunStatus
) -> Callable[[], Awaitable[bool]]:
    """True once the run reaches one of ``statuses`` *and* the worker has let go.

    Waiting on the status alone races the worker: a run whose status is written
    from outside - a cancellation - reaches it while the job is still in flight,
    and stopping there would cut the run off mid-node and assert on half of it.
    """

    async def condition() -> bool:
        row = await read_row(database, run_id)
        return RunStatus(row.status) in statuses and not harness.worker.busy

    return condition


def ago(seconds: float) -> dt.datetime:
    return utcnow() - dt.timedelta(seconds=seconds)
