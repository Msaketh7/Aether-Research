"""A worker's hold on a run, as transitions the database arbitrates.

Two workers can be handed the same run: the queue is at-least-once by design
(ADR 0005), and the reconciliation sweep deliberately re-dispatches runs it
cannot tell are still moving. Nothing here uses a lock to prevent that. Instead
every transition is one conditional ``UPDATE``: the row's current state is in
the WHERE clause, and a worker that changed no row has lost the argument and
drops the job.

That is the whole idempotency story. ``claim`` succeeds for exactly one worker;
``advance``, ``finish`` and ``release`` all name the caller's own ``worker_id``,
so a worker whose lease expired while it was stalled cannot overwrite the state
of the worker that took over from it.

The lease is ``worker_id`` plus ``heartbeat_at``. A worker renews the heartbeat
at every node boundary; when it stops renewing - killed, deployed over,
partitioned away - the sweep offers the run to whoever asks next, and the
LangGraph checkpoint means that worker resumes at the node the first one had not
finished rather than starting again.

**Every timestamp here is the database's.** Durations are passed in and turned
into intervals against ``now()`` inside the statement, so no comparison ever
straddles two clocks. Workers on different hosts would otherwise disagree about
when a lease expired by however far their clocks had drifted, and the one whose
clock ran fast would take runs from the others.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.core.enums import RunStatus
from app.research.schemas import ResearchRun

#: Statuses a run passes through while a worker holds it. Derived rather than
#: listed, so a new workflow phase cannot be forgotten here.
IN_FLIGHT_STATUSES: frozenset[RunStatus] = frozenset(
    status
    for status in RunStatus
    if not status.is_terminal and status not in {RunStatus.QUEUED, RunStatus.PAUSED}
)


@dataclass(frozen=True, slots=True)
class Lease:
    """One worker's claim on one run."""

    run: ResearchRun
    worker_id: str
    #: Which attempt this is, counting from 1. The worker compares it against
    #: the configured ceiling to decide whether a failure is worth retrying.
    attempt: int


@dataclass(frozen=True, slots=True)
class RunProgress:
    """What a run's row says while it is being executed.

    Written at every node boundary, so a person watching the run sees its
    counts move, and a run whose worker dies keeps the last ones it reached
    instead of reverting to zero.
    """

    status: RunStatus
    progress: float
    iterations: int
    sources: int
    claims: int
    contradictions: int
    total_tokens: int
    cost_usd: float


@dataclass(frozen=True, slots=True)
class RunFailure:
    """Why a run stopped, as the row records it."""

    code: str
    message: str


class RunLifecycle(Protocol):
    """The transitions a worker performs on a run it is executing."""

    async def claim(self, run_id: UUID, *, worker_id: str, lease_seconds: float) -> Lease | None:
        """Take the run, or return ``None`` because it is not this worker's to take.

        Claimable means: waiting (``queued``), due for a retry (``paused`` with
        no future ``next_attempt_at``), or held by a worker whose lease has
        expired. A terminal run is never claimable, which is what makes a
        redelivered job for a finished run a no-op rather than a second run.
        """
        ...

    async def advance(self, run_id: UUID, *, worker_id: str, progress: RunProgress) -> bool:
        """Renew the lease and record where the run has got to.

        ``False`` means the caller no longer holds the run - its lease expired,
        or the run was cancelled - and must stop.
        """
        ...

    async def finish(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        status: RunStatus,
        progress: float | None = None,
        coverage_caveat: str | None = None,
        error: RunFailure | None = None,
    ) -> bool:
        """End the run. ``False`` if something else already ended it."""
        ...

    async def release(
        self,
        run_id: UUID,
        *,
        worker_id: str,
        retry_in_seconds: float | None = None,
        refund_attempt: bool = False,
        error: RunFailure | None = None,
    ) -> bool:
        """Put the run back as ``paused``: not finished, and not a failure.

        ``retry_in_seconds`` is the backoff before it becomes eligible again;
        ``None`` means immediately. ``refund_attempt`` gives back the attempt the
        claim spent, which is right when a worker is shutting down and hands the
        run over - and wrong when the run failed, because then it was used.
        """
        ...

    async def due(
        self, *, lease_seconds: float, queued_grace_seconds: float, limit: int
    ) -> list[UUID]:
        """Runs that should be on the queue and may not be (ADR 0005).

        Three populations, and each is a message Redis can lose or a worker that
        can die: a run still ``queued`` long after it was created, a ``paused``
        run whose retry is due, and a run in flight whose lease has expired.
        """
        ...
