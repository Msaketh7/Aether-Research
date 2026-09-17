"""Worker entry point: ``python -m app.workers.runner``.

The second process type from ADR 0001. It assembles the object graph a research
run needs - gateway, toolbelt, ingestion, retrieval, the nine agents, the
checkpointer - once, and then spends its life in ``ResearchWorker.serve``.

**Everything is built once per process, and closed in the reverse order.** The
gateway owns the model concurrency semaphore and the toolbelt owns the guarded
HTTP client and its pool; one per run would mean one semaphore per run, which is
none. This mirrors what ``app.main.lifespan`` does for the API, and the two do
not share a process.

**Both signals stop it the same way.** SIGTERM is what an orchestrator sends
before it kills a container, SIGINT is Ctrl-C, and either means "stop taking
work and give back what you are holding". The handler only sets a flag; the
loop's drain does the rest, because a signal handler cannot await.

**The event loop is chosen, not inherited.** LangGraph's Postgres checkpointer
runs on psycopg, whose async mode refuses Windows' proactor loop - the default
there. A selector loop is the default on Linux anyway, so asking for one
explicitly costs nothing and makes a worker runnable on a developer machine.
"""

from __future__ import annotations

import asyncio
import signal
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.agents.checkpoint import open_checkpointer
from app.agents.factory import build_dependencies, build_research_nodes
from app.agents.graph import GraphBounds
from app.agents.runtime import ResearchGraphRunner
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.repositories.worker import SqlAlchemyRunLifecycle
from app.db.session import Database
from app.models import build_gateway
from app.research.cancellation import PostgresCancellationProbe
from app.research.recorder import RunRecorder
from app.retrieval.attached import AttachedUploadIngestion
from app.storage import build_object_storage
from app.workers.loop import ResearchWorker
from app.workers.queue import InMemoryJobQueue, JobQueue, RedisJobQueue, build_redis
from app.workers.worker import RunExecutor, worker_identity

logger = get_logger(__name__)

#: Headroom over the blocking reserve, so a quiet queue does not look like a
#: broken one. See ``RedisJobQueue.reserve``.
_SOCKET_TIMEOUT_MARGIN_SECONDS = 5.0


def build_queue(settings: Settings) -> JobQueue:
    """Redis everywhere except tests, exactly as the API chooses it.

    A worker holding the in-memory queue never sees what the API enqueued, since
    the two are different processes. It still executes runs, because its own
    reconciliation sweep finds them in Postgres once they have been queued for
    ``WORKER_QUEUED_GRACE_SECONDS`` and pushes them onto its own queue - which is
    what makes a worker runnable on a machine with no Redis, at the cost of that
    delay. In a deployment that delay would be the normal case, so this is
    selected only by ``APP_ENV=test`` and never by omission.
    """
    if settings.app_env == "test":
        return InMemoryJobQueue()
    return RedisJobQueue(
        build_redis(
            settings.redis_url,
            socket_timeout=settings.worker_poll_seconds + _SOCKET_TIMEOUT_MARGIN_SECONDS,
        )
    )


@asynccontextmanager
async def build_worker(settings: Settings) -> AsyncIterator[ResearchWorker]:
    """Everything a worker process owns, opened and closed in order."""
    database = Database(settings)
    queue = build_queue(settings)
    storage = build_object_storage(settings)
    gateway = build_gateway(settings)
    dependencies = build_dependencies(settings, gateway=gateway, database=database, storage=storage)
    try:
        async with open_checkpointer(settings) as checkpointer:
            worker = ResearchWorker(
                queue=queue,
                lifecycle=SqlAlchemyRunLifecycle(database),
                executor=RunExecutor(
                    lifecycle=SqlAlchemyRunLifecycle(database),
                    runner=ResearchGraphRunner(
                        nodes=build_research_nodes(settings, dependencies=dependencies),
                        checkpointer=checkpointer,
                        probe=PostgresCancellationProbe(database),
                        bounds=GraphBounds.from_settings(settings),
                        recorder=RunRecorder(database),
                    ),
                    uploads=AttachedUploadIngestion(
                        database=database,
                        storage=storage,
                        ingestor=dependencies.ingestor,
                    ),
                    settings=settings,
                ),
                settings=settings,
                worker_id=worker_identity(),
            )
            yield worker
    finally:
        # Stop reaching outwards first, then let go of the connections: a
        # closed pool underneath an in-flight fetch is a confusing error in the
        # logs of a process that is shutting down cleanly.
        await dependencies.close()
        await gateway.close()
        await queue.close()
        await storage.close()
        await database.dispose()


def install_signal_handlers(worker: ResearchWorker) -> None:
    """Ask the worker to stop on SIGTERM or SIGINT.

    ``signal.signal`` rather than the loop's own handler: the loop API is
    POSIX-only, and this is the one piece of the worker a developer runs on
    Windows. Setting a flag is all a handler may safely do.
    """
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda _signum, _frame: worker.stop())


async def serve(settings: Settings) -> None:
    async with build_worker(settings) as worker:
        install_signal_handlers(worker)
        await worker.serve()


def main() -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        asyncio.run(serve(settings), loop_factory=asyncio.SelectorEventLoop)
    except KeyboardInterrupt:  # pragma: no cover - a second Ctrl-C during drain
        logger.warning("worker interrupted during shutdown")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
