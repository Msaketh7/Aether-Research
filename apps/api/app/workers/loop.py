"""The loop a worker process spends its life in.

Three things happen here and nowhere else: jobs are taken off the queue, the
reconciliation sweep runs, and a stopping process gives back what it is holding.

**The sweep is why a lost message is not a lost run.** Redis is the dispatch
mechanism, not the source of truth (ADR 0005), so every worker periodically asks
Postgres which runs *should* be on the queue and puts them back: a run still
queued long after it was created, a paused run whose retry is due, and a run
whose worker stopped renewing its lease. Re-dispatching a run that is already
being executed is harmless - the claim is a conditional update and only one
worker can win it - which is what lets every worker sweep without coordinating.

**Draining is the restart guarantee.** On a signal the loop stops reserving,
gives its runs the grace period to reach a node boundary, then cancels what is
left and hands each one back as ``paused`` with its attempt refunded. The
cancelled run's checkpoint is already durable, so the worker that picks it up
resumes at the node the first one had not finished.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from app.core.config import Settings
from app.core.logging import get_logger
from app.observability.metrics import Metrics
from app.workers.lifecycle import Lease, RunLifecycle
from app.workers.queue import JobQueue
from app.workers.worker import Outcome, RunExecutor

logger = get_logger(__name__)


class ResearchWorker:
    """Consumes the research queue until it is asked to stop."""

    def __init__(
        self,
        *,
        queue: JobQueue,
        lifecycle: RunLifecycle,
        executor: RunExecutor,
        settings: Settings,
        worker_id: str,
        metrics: Metrics | None = None,
    ) -> None:
        self._metrics = metrics
        self._queue = queue
        self._lifecycle = lifecycle
        self._executor = executor
        self._settings = settings
        self._worker_id = worker_id
        self._stopping = asyncio.Event()
        self._slots = asyncio.Semaphore(settings.worker_concurrency)
        self._tasks: set[asyncio.Task[None]] = set()
        #: Runs this process currently holds a lease on, so a drain knows what
        #: to hand back. Keyed by run, because a job can only be held once.
        self._held: dict[UUID, Lease] = {}
        self._swept_at: float | None = None

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    @property
    def busy(self) -> bool:
        """Whether this worker is executing anything. Drives nothing; reported."""
        return bool(self._tasks)

    @property
    def active(self) -> int:
        """Runs this worker is executing right now, out of ``worker_concurrency``.

        The numerator of worker utilisation. Reported, never acted on: the
        semaphore is what bounds the work, and a second count used to make
        decisions would be the one that drifts.
        """
        return len(self._tasks)

    def stop(self) -> None:
        """Ask the loop to finish. Safe from a signal handler."""
        self._stopping.set()

    async def serve(self) -> None:
        """Reserve and execute until stopped, then drain."""
        logger.info(
            "worker starting",
            extra={
                "worker_id": self._worker_id,
                "concurrency": self._settings.worker_concurrency,
            },
        )
        try:
            while not self.stopping:
                await self.reconcile()
                if not await self._take_slot():
                    continue
                await self._dispatch()
        finally:
            await self.drain()
            logger.info("worker stopped", extra={"worker_id": self._worker_id})

    # --- one turn of the loop ---------------------------------------------

    async def _take_slot(self) -> bool:
        """Claim capacity to run one more research, or report that there is none.

        When every slot is busy the loop waits on the running tasks rather than
        on the queue: reserving a job it could not start would take it off the
        queue and leave it to the sweep to rediscover.
        """
        if self._slots.locked():
            if self._tasks:
                await asyncio.wait(
                    self._tasks,
                    timeout=self._settings.worker_poll_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            return False
        await self._slots.acquire()
        return True

    async def _dispatch(self) -> None:
        """Wait for a job and start it. Releases the slot if none arrives."""
        try:
            run_id = await self._queue.reserve(timeout_seconds=self._settings.worker_poll_seconds)
        except Exception:
            self._slots.release()
            # A queue outage must not spin the loop at full speed. The poll
            # interval is the right pause: it is how long an idle worker would
            # have waited anyway.
            logger.exception(
                "could not reserve a research job", extra={"worker_id": self._worker_id}
            )
            await asyncio.sleep(self._settings.worker_poll_seconds)
            return
        if run_id is None:
            self._slots.release()
            return

        task = asyncio.create_task(self._handle(run_id), name=f"research-{run_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _handle(self, run_id: UUID) -> None:
        """Claim the run and execute it. Never raises except on cancellation."""
        try:
            lease = await self._lifecycle.claim(
                run_id,
                worker_id=self._worker_id,
                lease_seconds=self._settings.worker_lease_seconds,
            )
            if lease is None:
                # Finished, cancelled, or already someone else's. A duplicate
                # delivery ends here, which is the whole of job idempotency.
                logger.info(
                    "research job dropped",
                    extra={"run_id": str(run_id), "worker_id": self._worker_id},
                )
                return
            self._held[run_id] = lease
            try:
                outcome = await self._executor.execute(lease)
            except asyncio.CancelledError:
                # Left in `_held`: the drain hands it back.
                raise
            except Exception:
                # The executor records its own failures, so reaching here is a
                # bug in the recording itself. The run keeps its lease and the
                # sweep recovers it once the lease expires.
                self._held.pop(run_id, None)
                logger.exception(
                    "a research job ended without recording an outcome",
                    extra={"run_id": str(run_id)},
                )
                return
            self._held.pop(run_id, None)
            _log_outcome(outcome)
        finally:
            self._slots.release()

    # --- reconciliation ---------------------------------------------------

    async def reconcile(self) -> int:
        """Re-dispatch runs the queue may have lost. Returns how many."""
        if not self._sweep_due():
            return 0
        self._swept_at = asyncio.get_running_loop().time()
        await self._gauge()
        try:
            due = await self._lifecycle.due(
                lease_seconds=self._settings.worker_lease_seconds,
                queued_grace_seconds=self._settings.worker_queued_grace_seconds,
                limit=self._settings.worker_sweep_batch,
            )
            for run_id in due:
                await self._queue.enqueue(run_id)
        except Exception:
            # The sweep is a repair mechanism; a failed repair must not stop the
            # worker doing the work it already has.
            logger.exception("reconciliation sweep failed", extra={"worker_id": self._worker_id})
            return 0
        if due:
            logger.info(
                "runs re-dispatched by reconciliation",
                extra={"count": len(due), "worker_id": self._worker_id},
            )
        return len(due)

    async def _gauge(self) -> None:
        """Publish the queue depth on the sweep's tick.

        On the sweep rather than on every reserve: the depth is a gauge a
        human reads on a dashboard, and one reading every thirty seconds is
        the resolution that question has. It also means the extra round trip
        happens exactly as often as the sweep's own.
        """
        if self._metrics is None:
            return
        try:
            self._metrics.queue_depth.set(await self._queue.depth())
        except Exception:
            # A depth nobody could read is left at its last value rather than
            # zeroed: a graph that drops to zero says the backlog cleared.
            logger.debug("could not read the queue depth for the gauge")

    def _sweep_due(self) -> bool:
        now = asyncio.get_running_loop().time()
        if self._swept_at is None:
            return True
        return now - self._swept_at >= self._settings.worker_sweep_interval_seconds

    # --- stopping ---------------------------------------------------------

    async def drain(self) -> None:
        """Let the in-flight runs reach a checkpoint, then hand back the rest."""
        self._stopping.set()
        if self._tasks:
            _, pending = await asyncio.wait(
                self._tasks, timeout=self._settings.worker_shutdown_grace_seconds
            )
            for task in pending:
                task.cancel()
            if pending:
                logger.info(
                    "cancelling research runs at their last checkpoint",
                    extra={"count": len(pending), "worker_id": self._worker_id},
                )
                await asyncio.gather(*pending, return_exceptions=True)

        # Whatever is still held was cancelled, or its task died without
        # recording an outcome. Either way this worker is not going to finish
        # it, and a run left holding an expired lease waits for the sweep.
        for lease in list(self._held.values()):
            await self._hand_back(lease)
        self._held.clear()

    async def _hand_back(self, lease: Lease) -> None:
        """Return a run to the queue as ``paused``, not as a failure.

        The attempt is refunded because nothing about the run failed: this
        process was asked to stop. A worker that is *killed* refunds nothing,
        which is what keeps a run that crashes its worker from being retried
        forever.
        """
        try:
            released = await self._lifecycle.release(
                lease.run.id, worker_id=self._worker_id, refund_attempt=True
            )
            if released:
                await self._queue.enqueue(lease.run.id)
        except Exception:
            # The run keeps its lease and its status. The sweep re-dispatches it
            # once the lease expires, which is slower but never wrong.
            logger.exception(
                "could not hand back a research run",
                extra={"run_id": str(lease.run.id), "worker_id": self._worker_id},
            )
            return
        logger.info(
            "research run handed back",
            extra={
                "run_id": str(lease.run.id),
                "attempt": lease.attempt,
                "released": released,
            },
        )


def _log_outcome(outcome: Outcome) -> None:
    logger.info(
        "research job done",
        extra={
            "run_id": str(outcome.run_id),
            "status": None if outcome.status is None else outcome.status.value,
            "detail": outcome.detail,
        },
    )
