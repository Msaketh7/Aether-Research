"""The worker's writes to ``research_runs``, as conditional updates.

Implements ``app.workers.lifecycle.RunLifecycle``. Every statement here is a
single ``UPDATE ... WHERE ... RETURNING``, for one reason: the worker is not the
only process that can touch a run. A second worker may have been handed the same
job, the user may have cancelled it, and the reconciliation sweep may have
offered it to somebody else. Read-then-write would lose all three races; a
conditional update settles each in the database, and the caller learns which way
it went from whether a row came back.

A session per call, not per request. The worker has no request boundary, and a
lease renewal must be committed the moment it is made - holding it open until the
end of a five-minute run would defeat the point of writing it at all.

Every timestamp is ``now()`` evaluated by Postgres, and every deadline is an
interval from it. Workers on different hosts therefore agree about when a lease
expired, however far their own clocks have drifted from each other's.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import Update, and_, func, or_, select, update
from sqlalchemy.sql.elements import ColumnElement

from app.core.enums import RunStatus
from app.db.models.research import ResearchRunRow
from app.db.repositories.research import to_run_dto
from app.db.session import Database
from app.workers.lifecycle import IN_FLIGHT_STATUSES, Lease, RunFailure, RunProgress

_TERMINAL = [status.value for status in RunStatus if status.is_terminal]
_IN_FLIGHT = [status.value for status in IN_FLIGHT_STATUSES]

#: The status a claimed run takes. Not a guess about what happens next: the
#: planner is the graph's entry node, so a run a worker has just picked up is
#: planning until the first node boundary says otherwise.
CLAIMED_STATUS = RunStatus.PLANNING


def _error_json(error: RunFailure | None) -> dict[str, str] | None:
    return None if error is None else {"code": error.code, "message": error.message}


def _ago(seconds: float) -> ColumnElement[dt.datetime]:
    """A deadline in the past, measured by the database rather than by a host."""
    return func.now() - dt.timedelta(seconds=seconds)


def _ahead(seconds: float) -> ColumnElement[dt.datetime]:
    return func.now() + dt.timedelta(seconds=seconds)


def _not_already_queued(forgotten: ColumnElement[dt.datetime]) -> ColumnElement[bool]:
    """Whether this run may be put on the queue again (Phase 22).

    The three disjuncts are, in order: never dispatched; something has happened
    to it since the last dispatch; that dispatch is old enough to be presumed
    lost. See ``SqlAlchemyRunLifecycle.due``.
    """
    return or_(
        ResearchRunRow.last_queued_at.is_(None),
        ResearchRunRow.last_queued_at <= ResearchRunRow.updated_at,
        ResearchRunRow.last_queued_at <= forgotten,
    )


class SqlAlchemyRunLifecycle:
    """Claims, heartbeats, releases and finishes runs for one worker process."""

    def __init__(self, database: Database) -> None:
        self._database = database

    # --- taking the run ---------------------------------------------------

    async def claim(
        self, run_id: uuid.UUID, *, worker_id: str, lease_seconds: float
    ) -> Lease | None:
        expired = _ago(lease_seconds)
        statement = (
            update(ResearchRunRow)
            .where(
                ResearchRunRow.id == run_id,
                or_(
                    # Waiting, or a retry that has come due.
                    and_(
                        ResearchRunRow.status.in_([RunStatus.QUEUED.value, RunStatus.PAUSED.value]),
                        or_(
                            ResearchRunRow.next_attempt_at.is_(None),
                            ResearchRunRow.next_attempt_at <= func.now(),
                        ),
                    ),
                    # Held by a worker that has stopped proving it is alive.
                    and_(
                        ResearchRunRow.status.in_(_IN_FLIGHT),
                        or_(
                            ResearchRunRow.heartbeat_at.is_(None),
                            ResearchRunRow.heartbeat_at <= expired,
                        ),
                    ),
                ),
            )
            .values(
                status=CLAIMED_STATUS.value,
                attempts=ResearchRunRow.attempts + 1,
                worker_id=worker_id,
                heartbeat_at=func.now(),
                next_attempt_at=None,
                # First claim starts the clock; a resume keeps the original, so
                # the elapsed time a user sees spans the whole run.
                started_at=func.coalesce(ResearchRunRow.started_at, func.now()),
                langgraph_thread_id=str(run_id),
                # The previous attempt's failure is history the moment this one
                # begins. Leaving it would show a running run as broken.
                error=None,
                updated_at=func.now(),
            )
            .returning(ResearchRunRow)
        )
        async with self._database.session() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
            if row is None:
                return None
            lease = Lease(run=to_run_dto(row), worker_id=worker_id, attempt=row.attempts)
        return lease

    # --- holding it -------------------------------------------------------

    async def advance(self, run_id: uuid.UUID, *, worker_id: str, progress: RunProgress) -> bool:
        statement = self._held(run_id, worker_id).values(
            status=progress.status.value,
            progress=progress.progress,
            heartbeat_at=func.now(),
            iteration_count=progress.iterations,
            source_count=progress.sources,
            claim_count=progress.claims,
            contradiction_count=progress.contradictions,
            total_tokens=progress.total_tokens,
            total_cost_usd=progress.cost_usd,
            updated_at=func.now(),
        )
        return await self._apply(statement)

    # --- letting it go ----------------------------------------------------

    async def finish(
        self,
        run_id: uuid.UUID,
        *,
        worker_id: str,
        status: RunStatus,
        progress: float | None = None,
        coverage_caveat: str | None = None,
        error: RunFailure | None = None,
    ) -> bool:
        values: dict[str, Any] = {
            "status": status.value,
            "completed_at": func.now(),
            "worker_id": None,
            "heartbeat_at": None,
            "next_attempt_at": None,
            "error": _error_json(error),
            "updated_at": func.now(),
        }
        if progress is not None:
            values["progress"] = progress
        if coverage_caveat is not None:
            values["coverage_caveat"] = coverage_caveat
        return await self._apply(self._held(run_id, worker_id).values(**values))

    async def release(
        self,
        run_id: uuid.UUID,
        *,
        worker_id: str,
        retry_in_seconds: float | None = None,
        refund_attempt: bool = False,
        error: RunFailure | None = None,
    ) -> bool:
        values: dict[str, Any] = {
            "status": RunStatus.PAUSED.value,
            "worker_id": None,
            "heartbeat_at": None,
            "next_attempt_at": None if retry_in_seconds is None else _ahead(retry_in_seconds),
            "error": _error_json(error),
            "updated_at": func.now(),
        }
        if refund_attempt:
            # GREATEST rather than a plain subtraction: a refund can only ever
            # follow a claim, but a counter that can go negative is a counter
            # that will eventually read -1 and make a ceiling meaningless.
            values["attempts"] = func.greatest(ResearchRunRow.attempts - 1, 0)
        return await self._apply(self._held(run_id, worker_id).values(**values))

    # --- reconciliation ---------------------------------------------------

    async def due(
        self, *, lease_seconds: float, queued_grace_seconds: float, limit: int
    ) -> list[uuid.UUID]:
        """The runs that should be on the queue and are not, stamped as dispatched.

        A write, not a read, and that is the point. Phase 21's load test
        measured the read version putting **4,662 entries on the queue for 100
        offered jobs**: a run that is still ``queued`` because every worker slot
        is busy answers "yes, I should be on the queue" on every sweep, and
        neither the sweep nor the queue deduplicates. Nothing ran twice - the
        claim is a conditional update and the duplicate delivery is dropped -
        but ``queue_depth`` is the gauge on the dashboard and the obvious input
        to an autoscaler, and under Redis the list grew without bound for as
        long as the backlog lasted.

        So the sweep now remembers. A run is due when it is in a state that
        belongs on the queue **and** one of three things is true:

        * it has never been dispatched by a sweep (``last_queued_at IS NULL``);
        * **something has happened to it since** the last dispatch
          (``last_queued_at <= updated_at``) - which is what a worker handing it
          back, a retry coming due and an expired lease all look like, so none
          of those waits;
        * the last dispatch is older than the grace period, so it was evidently
          lost, which is the case this repair mechanism exists for.

        Stamping and selecting in one statement, with ``SKIP LOCKED`` on the
        rows being chosen, so two workers sweeping at the same instant do not
        both dispatch the same run - the older read-then-enqueue could.
        """
        expired = _ago(lease_seconds)
        forgotten = _ago(queued_grace_seconds)
        selected = (
            select(ResearchRunRow.id)
            .where(
                or_(
                    # Enqueued, and nothing has picked it up. The grace period
                    # is what keeps a run created a moment ago from being
                    # dispatched twice.
                    and_(
                        ResearchRunRow.status == RunStatus.QUEUED.value,
                        ResearchRunRow.created_at <= forgotten,
                    ),
                    # A retry whose backoff has elapsed.
                    and_(
                        ResearchRunRow.status == RunStatus.PAUSED.value,
                        or_(
                            ResearchRunRow.next_attempt_at.is_(None),
                            ResearchRunRow.next_attempt_at <= func.now(),
                        ),
                    ),
                    # In flight, but its worker has stopped saying so.
                    and_(
                        ResearchRunRow.status.in_(_IN_FLIGHT),
                        or_(
                            ResearchRunRow.heartbeat_at.is_(None),
                            ResearchRunRow.heartbeat_at <= expired,
                        ),
                    ),
                ),
                _not_already_queued(forgotten),
            )
            # Oldest first: a run that has been waiting longest is the one a
            # user has been watching longest.
            .order_by(ResearchRunRow.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        statement = (
            update(ResearchRunRow)
            .where(ResearchRunRow.id.in_(selected.scalar_subquery()))
            # `updated_at` is pinned to itself, not left out. `UpdatedAtMixin`
            # declares an application-side `onupdate`, and SQLAlchemy applies
            # that to a Core UPDATE as well as to an ORM flush - so omitting it
            # would stamp `updated_at` too, make it equal `last_queued_at`, and
            # satisfy the very rule below on the next sweep. The fix would have
            # been a no-op that looked correct. Queueing a run again is not a
            # change to the run.
            .values(last_queued_at=func.now(), updated_at=ResearchRunRow.updated_at)
            .returning(ResearchRunRow.id)
        )
        async with self._database.session() as session:
            return list((await session.execute(statement)).scalars())

    # --- internals --------------------------------------------------------

    def _held(self, run_id: uuid.UUID, worker_id: str) -> Update:
        """The precondition every write shares: this worker still holds this run.

        ``status NOT IN terminal`` is the other half. A user's cancellation and
        a worker's own finish both land there, and neither may be overwritten by
        a worker that was mid-node when it happened.
        """
        return update(ResearchRunRow).where(
            ResearchRunRow.id == run_id,
            ResearchRunRow.worker_id == worker_id,
            ResearchRunRow.status.not_in(_TERMINAL),
        )

    async def _apply(self, statement: Update) -> bool:
        async with self._database.session() as session:
            result = await session.execute(statement.returning(ResearchRunRow.id))
            return result.scalar_one_or_none() is not None
