"""The worker loop: take a run off the queue, execute it, record what happened.

This is the second process type from ADR 0001, and the thing the whole layering
above it exists to make possible - the API never runs a research workflow, so
something has to.

**One run's journey.** Reserve an id from the queue; claim the run in Postgres,
which is what settles a duplicate delivery; ingest the files it was created with;
run the graph, renewing the lease and writing the run's counts at every node
boundary; record the outcome. Nothing in that sequence assumes it is the first
attempt: the claim resumes a paused run, ingestion is idempotent, and the graph
resumes from its checkpoint.

**Failure is a decision about what can still be produced.** A ``GraphError``
that says it is retryable, or an infrastructure failure, pauses the run with a
backoff and puts it back - unless it has used its attempts, at which point it is
marked failed with the reason. A failure the graph calls unretryable, such as a
node breaking its contract, fails immediately: doing it again produces the same
bug and spends the same money.

**Shutting down is not failing.** On a signal the loop stops reserving, waits for
its runs to reach a checkpoint, and hands back whatever has not finished as
``paused`` - the attempt refunded, since it was not spent on a failure. Another
worker picks the run up and continues from the node the first one had not
reached. That is the whole of "research must survive worker restarts", and it
rests on ``durability="sync"`` in the runner: the checkpoint is written before
the next node starts, so the work already done is never the work that is lost.
"""

from __future__ import annotations

import os
import socket
import uuid
from dataclasses import dataclass

from app.agents.errors import GraphError
from app.agents.runtime import ResearchGraphRunner
from app.agents.schemas import GraphNode, StopReason
from app.agents.state import ResearchState, RunBrief, stop_reason
from app.core.config import Settings
from app.core.enums import RunStatus
from app.core.logging import get_logger
from app.research.schemas import ResearchRun
from app.retrieval.attached import AttachedUploadIngestion
from app.workers.lifecycle import Lease, RunFailure, RunLifecycle, RunProgress
from app.workers.progress import furthest, progress_for, status_for

logger = get_logger(__name__)


def worker_identity() -> str:
    """A name for this process that a person can act on.

    Host and pid, plus a random suffix because a container can be replaced by
    one with the same pid on the same host. Short enough for the column, and
    stable for the life of the process - the lease depends on that.
    """
    return f"{socket.gethostname()[:32]}-{os.getpid()}-{uuid.uuid4().hex[:8]}"[:64]


class LeaseLost(Exception):
    """This worker no longer holds the run, so it must stop touching it.

    Two things land here and they are both normal: a user cancelled the run, or
    the lease expired and another worker took it over. Raised from inside the
    graph's step listener, which is the only place a worker can interrupt a run
    between nodes - so the run stops with its checkpoint intact.

    Not an ``AppError``: nothing about it reaches a client, and it is not a
    failure of the run. It is the run ceasing to be this worker's business.
    """


@dataclass(frozen=True, slots=True)
class Outcome:
    """How one job ended, for the log line and for the tests."""

    run_id: uuid.UUID
    #: ``None`` when this worker did not decide the run's fate - it stopped
    #: because the run was no longer its own.
    status: RunStatus | None
    detail: str


class RunExecutor:
    """Executes one claimed run, and writes what happened to its row."""

    def __init__(
        self,
        *,
        lifecycle: RunLifecycle,
        runner: ResearchGraphRunner,
        uploads: AttachedUploadIngestion,
        settings: Settings,
    ) -> None:
        self._lifecycle = lifecycle
        self._runner = runner
        self._uploads = uploads
        self._settings = settings

    async def execute(self, lease: Lease) -> Outcome:
        """Run it, and write the result. Cancellation is left to propagate.

        A cancelled task is this process being torn down, and the loop that
        cancelled it is what hands the run back (``app.workers.loop``). Doing it
        here would mean awaiting two more writes inside a cancelled task, which
        is exactly the thing that does not reliably finish.
        """
        run = lease.run
        try:
            brief = await self._brief(run)
            state = await self._runner.run(brief, listener=_StepReporter(self, lease))
        except LeaseLost:
            # Cancelled, or taken over. Either way the row already says what it
            # should, and writing to it now would be this worker overwriting a
            # decision it did not make.
            logger.info(
                "research run is no longer this worker's",
                extra={"run_id": str(run.id), "worker_id": lease.worker_id},
            )
            return Outcome(run.id, None, "no longer held")
        except Exception as exc:
            return await self._failed(lease, exc)
        return await self._completed(lease, state)

    # --- the parts ---------------------------------------------------------

    async def _brief(self, run: ResearchRun) -> RunBrief:
        """Ingest the run's attached files, then describe the run to the graph.

        Ingestion happens here rather than at upload time (ADR 0012): it is
        minutes of parsing and embedding, and a request thread is the one place
        it must not happen. A document that cannot be read is recorded and
        skipped by the ingester; ``has_attached_documents`` reports whether any
        of them actually made it in, because the document researcher searching a
        corpus that does not exist is worse than not running at all.
        """
        results = await self._uploads.ingest_run(run.id, user_id=run.user_id)
        ingested = sum(1 for result in results if result.outcome is not None)
        if results:
            logger.info(
                "attached documents ingested",
                extra={
                    "run_id": str(run.id),
                    "attached": len(results),
                    "ingested": ingested,
                },
            )
        return RunBrief.from_run(run, has_attached_documents=ingested > 0)

    async def _completed(self, lease: Lease, state: ResearchState) -> Outcome:
        """Close a run the graph returned from.

        Returning is not the same as succeeding. A cancelled run also returns -
        the graph routes to the end without a report - and its row already says
        ``cancelled``, which ``finish`` will not overwrite.
        """
        cancelled = stop_reason(state) is StopReason.CANCELLED
        status = RunStatus.CANCELLED if cancelled else RunStatus.COMPLETED
        stop = state.get("stop")
        await self._lifecycle.finish(
            lease.run.id,
            worker_id=lease.worker_id,
            status=status,
            progress=None if cancelled else 1.0,
            coverage_caveat=None if stop is None else stop.caveat,
        )
        logger.info(
            "research run finished",
            extra={
                "run_id": str(lease.run.id),
                "status": status.value,
                "attempt": lease.attempt,
                "claims": len(state.get("claims") or ()),
                "sources": len(state.get("sources") or ()),
            },
        )
        return Outcome(lease.run.id, status, status.value)

    async def _failed(self, lease: Lease, exc: Exception) -> Outcome:
        """Retry the run, or give up on it and say why.

        The graph's own taxonomy decides which: a ``GraphError`` knows whether
        running it again could work. Anything else is an infrastructure failure -
        the database, the object store, the queue - and those are exactly the
        failures a retry is for.
        """
        retryable = exc.retryable if isinstance(exc, GraphError) else True
        failure = _as_failure(exc)
        if failure.code == "worker_error":
            # Only the stored message is sanitised. An unexpected exception has
            # to reach the logs with its traceback or there is nothing to debug
            # from, and the logs are inside the trust boundary the message is
            # crossing (ADR 0011).
            logger.exception(
                "a research run stopped on an unexpected error",
                extra={"run_id": str(lease.run.id), "attempt": lease.attempt},
            )
        attempts_left = self._settings.worker_max_attempts - lease.attempt
        if retryable and attempts_left > 0:
            backoff = self._backoff(lease)
            await self._lifecycle.release(
                lease.run.id,
                worker_id=lease.worker_id,
                retry_in_seconds=backoff,
                error=failure,
            )
            logger.warning(
                "research run paused for retry",
                extra={
                    "run_id": str(lease.run.id),
                    "attempt": lease.attempt,
                    "attempts_left": attempts_left,
                    "code": failure.code,
                    "retry_in_seconds": backoff,
                },
            )
            return Outcome(lease.run.id, RunStatus.PAUSED, f"retrying after {failure.code}")

        await self._lifecycle.finish(
            lease.run.id,
            worker_id=lease.worker_id,
            status=RunStatus.FAILED,
            error=failure,
        )
        logger.error(
            "research run failed",
            extra={
                "run_id": str(lease.run.id),
                "attempt": lease.attempt,
                "code": failure.code,
                "retryable": retryable,
            },
        )
        return Outcome(lease.run.id, RunStatus.FAILED, failure.code)

    def _backoff(self, lease: Lease) -> float:
        """Exponential, capped, and spread out by the run's own id.

        The spread matters because the failures this retries are rarely
        independent: a model provider going down fails every run in flight at
        about the same moment, and without it they would all come back at the
        same moment too. The offset is derived from the run id rather than drawn
        at random - the ids are already uniformly distributed, and a backoff that
        is the same every time is one a test can assert and an operator can
        predict.
        """
        delay = self._settings.worker_retry_base_delay_seconds * 2.0 ** max(0, lease.attempt - 1)
        # 1.0 to 1.5 times the delay. Never below it, so the base is a floor
        # rather than an average.
        spread = 1.0 + (lease.run.id.int % 512) / 1024
        return min(delay * spread, self._settings.worker_retry_max_delay_seconds)

    async def advanced(
        self, lease: Lease, nodes: tuple[GraphNode, ...], state: ResearchState
    ) -> None:
        """One node boundary: renew the lease, and say where the run has got to.

        Raising ``LeaseLost`` here is what stops a run this worker no longer
        owns. It travels out through the graph, so the run stops at a node
        boundary with its checkpoint intact rather than being torn out of one.
        """
        node = furthest(nodes)
        held = await self._lifecycle.advance(
            lease.run.id,
            worker_id=lease.worker_id,
            progress=RunProgress(
                status=status_for(node),
                progress=progress_for(
                    node,
                    iteration=state.get("iteration", 0),
                    max_iterations=lease.run.limits.max_iterations,
                ),
                iterations=state.get("iteration", 0),
                sources=len(state.get("sources") or ()),
                claims=len(state.get("claims") or ()),
                contradictions=len(state.get("contradictions") or ()),
                total_tokens=_tokens(state),
                cost_usd=_cost(state),
            ),
        )
        if not held:
            raise LeaseLost(str(lease.run.id))


@dataclass(frozen=True, slots=True)
class _StepReporter:
    """One lease's ``StepListener``: what the graph reports, the run's row records."""

    executor: RunExecutor
    lease: Lease

    async def stepped(self, nodes: tuple[GraphNode, ...], state: ResearchState) -> None:
        await self.executor.advanced(self.lease, nodes, state)


def _tokens(state: ResearchState) -> int:
    usage = state.get("token_usage")
    return 0 if usage is None else usage.total


def _cost(state: ResearchState) -> float:
    estimate = state.get("estimated_cost")
    return 0.0 if estimate is None else estimate.usd


def _as_failure(exc: Exception) -> RunFailure:
    """What the run's row says went wrong.

    A ``GraphError`` carries a message written to be read by the person who
    asked the question. Anything else does not, so only its class name is
    recorded: an arbitrary exception string can carry a database URL, a file
    path, or a fragment of a page the run fetched (ADR 0011).
    """
    if isinstance(exc, GraphError):
        return RunFailure(code=exc.code, message=exc.message)
    return RunFailure(
        code="worker_error",
        message="The research run stopped because of an internal error.",
    )
