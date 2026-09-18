"""The progress stream as rows: append, and replay.

Implements ``app.research.eventbus.EventLog``. Two statements, and the whole
design is in the first one.

**The insert allocates the number, and a lock makes that atomic.** ``seq`` is
chosen by the same statement that stores the event, from the maximum already
recorded for that run, so there is no window in which a number exists but the
event does not. Two emitters reading the same maximum would produce the same
number, so the append first takes a transaction-scoped advisory lock keyed by
the run: appends for one run are serialised, appends for different runs are
not, and the lock is released by the commit that stores the event.

It was written first with only the unique constraint and a bounded retry, on
the reasoning that contention is one event wide - a run has a single worker
(ADR 0017) and the only other emitter is the API cancelling it. Eight
concurrent appends in a test exhausted the retries, which is the useful kind
of wrong answer: a retry count is a guess about contention, and a lock is not.
The constraint and the retry both stay, as the thing that would catch an
emitter that skipped the lock.

The statement is written out rather than assembled, because what it has to be
is exact: an `INSERT ... SELECT` whose aggregate reads the table it is
inserting into. Expressed through the query builder, the correlation rules
decide whether that sub-select keeps its own `FROM`, and a silent change there
would be a duplicate sequence rather than an error. The parameters are bound,
and nothing in them comes from outside the system.

**A session per call.** These are called from a worker with no request
boundary, and an event that is not committed as it happens is an event the
stream cannot replay after a crash. It also keeps the event out of the caller's
transaction, which matters in the other direction: a run's own write failing
must not silently delete the record of what was streamed about it.
"""

from __future__ import annotations

import uuid

from pydantic_core import to_json
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.core.enums import ResearchEventType, RunStatus
from app.core.logging import get_logger
from app.db.models.event import ResearchEventRow
from app.db.session import Database
from app.research.events import EventDraft, ResearchEvent

logger = get_logger(__name__)

#: Attempts at allocating a sequence number before giving up. With the lock
#: below, a retry means an emitter that did not take it - a bug rather than
#: contention - so a small number is right.
MAX_ALLOCATION_ATTEMPTS = 3

#: Events one replay may return. A run that emitted more than this has streamed
#: for a very long time; a reconnecting client is served the oldest window it
#: has not seen, and reconnects again for the rest.
DEFAULT_REPLAY_LIMIT = 2000

#: Postgres' unique_violation. The only integrity failure a retry can fix: a
#: violated foreign key means the run is gone, and asking again will not
#: bring it back.
_UNIQUE_VIOLATION = "23505"

#: Serialises appends for one run, and only for one run. Transaction-scoped,
#: so the commit that stores the event releases it - there is no path where a
#: failed append leaves the lock held. Two runs whose ids hash alike share a
#: lock, which costs a little serialisation and is never wrong.
#:
#: The id is bound as text, not as a uuid: asyncpg infers a parameter's type
#: from where it is used, and ``hashtextextended`` wants text.
_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:run_key, 0))")

_APPEND = text(
    """
    INSERT INTO research_events (run_id, seq, type, status, payload, occurred_at)
    SELECT :run_id, COALESCE(MAX(seq), 0) + 1, :type, :status, CAST(:payload AS jsonb), now()
      FROM research_events
     WHERE run_id = :run_id
    RETURNING seq, occurred_at
    """
)


class SqlAlchemyEventLog:
    """Stores and replays a run's events."""

    def __init__(self, database: Database, *, replay_limit: int = DEFAULT_REPLAY_LIMIT) -> None:
        self._database = database
        self._replay_limit = replay_limit

    async def append(self, draft: EventDraft) -> ResearchEvent:
        """Store the event, numbered, and return it as it was stored.

        ``occurred_at`` is the database's clock rather than the emitter's:
        workers on different hosts write to one stream, and a reader ordering
        by time should not see it move backwards because one host has drifted.
        """
        parameters = {
            "run_id": draft.run_id,
            "type": draft.type.value,
            "status": draft.status.value,
            "payload": to_json(draft.payload).decode(),
        }
        for attempt in range(1, MAX_ALLOCATION_ATTEMPTS + 1):
            try:
                async with self._database.session() as session:
                    await session.execute(_LOCK, {"run_key": str(draft.run_id)})
                    row = (await session.execute(_APPEND, parameters)).one()
            except IntegrityError as exc:
                if not _is_unique_violation(exc) or attempt == MAX_ALLOCATION_ATTEMPTS:
                    raise
                # Another emitter took the number between the aggregate and the
                # insert. Re-reading the maximum is the whole of the retry.
                logger.info(
                    "event sequence contended; retrying",
                    extra={"run_id": str(draft.run_id), "attempt": attempt},
                )
                continue
            return ResearchEvent.of(draft, seq=int(row.seq), at=row.occurred_at)

        raise AssertionError("unreachable: the loop returns or raises")  # pragma: no cover

    async def replay(
        self, run_id: uuid.UUID, *, after_seq: int = 0, limit: int | None = None
    ) -> list[ResearchEvent]:
        """This run's events after ``after_seq``, oldest first and bounded."""
        statement = (
            select(ResearchEventRow)
            .where(ResearchEventRow.run_id == run_id, ResearchEventRow.seq > after_seq)
            .order_by(ResearchEventRow.seq)
            .limit(min(limit or self._replay_limit, self._replay_limit))
        )
        async with self._database.session() as session:
            rows = (await session.execute(statement)).scalars().all()
        return [_to_event(row) for row in rows]

    async def last_seq(self, run_id: uuid.UUID) -> int:
        """The highest number this run has used. 0 when it has emitted nothing."""
        statement = select(func.coalesce(func.max(ResearchEventRow.seq), 0)).where(
            ResearchEventRow.run_id == run_id
        )
        async with self._database.session() as session:
            return int((await session.execute(statement)).scalar_one())


def _is_unique_violation(exc: IntegrityError) -> bool:
    """Whether the driver said this was a duplicate key rather than anything else."""
    return getattr(exc.orig, "sqlstate", None) == _UNIQUE_VIOLATION


def _to_event(row: ResearchEventRow) -> ResearchEvent:
    return ResearchEvent(
        seq=row.seq,
        type=ResearchEventType(row.type),
        run_id=row.run_id,
        at=row.occurred_at,
        status=RunStatus(row.status),
        payload=dict(row.payload),
    )
