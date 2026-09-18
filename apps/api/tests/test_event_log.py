"""The durable progress log and the broker built on it (Phase 14, ADR 0006).

These run against real Postgres, because the whole point of the log is the
guarantee the database makes: ``(run_id, seq)`` is unique, and the insert that
stores an event is what allocates its number. A fake store would satisfy the
protocol and prove none of it.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest

from app.core.enums import ResearchEventType, RunStatus
from app.db.repositories.events import SqlAlchemyEventLog
from app.research.eventbus import DurableEventBroker, build_event_broker
from app.research.events import EventDraft, InMemoryEventBroker, ResearchEvent
from tests.support.worker import seed_run, worker_settings


@pytest.fixture
def worker_config(settings):
    return worker_settings(settings)


def draft(run_id: uuid.UUID, **overrides: object) -> EventDraft:
    fields: dict[str, object] = {
        "type": ResearchEventType.SOURCE_FOUND,
        "run_id": run_id,
        "status": RunStatus.RESEARCHING,
        "payload": {"source_id": str(uuid.uuid4()), "title": "A page"},
    }
    fields.update(overrides)
    return EventDraft(**fields)  # type: ignore[arg-type]


# --- the log --------------------------------------------------------------


async def test_the_insert_allocates_the_sequence(database, worker_config):
    run = await seed_run(database, worker_config)
    log = SqlAlchemyEventLog(database)

    first = await log.append(draft(run.id))
    second = await log.append(draft(run.id))

    assert (first.seq, second.seq) == (1, 2)
    assert await log.last_seq(run.id) == 2


async def test_two_emitters_never_share_a_number(database, worker_config):
    """The failure a per-process counter ships: one ``id:`` for two events.

    Both emitters read the same maximum; the unique constraint rejects one and
    the retry re-reads. What must come out is a gapless run of distinct
    numbers, whatever order they were allocated in.
    """
    run = await seed_run(database, worker_config)
    log = SqlAlchemyEventLog(database)

    events = await asyncio.gather(*(log.append(draft(run.id)) for _ in range(8)))

    assert sorted(event.seq for event in events) == list(range(1, 9))


async def test_a_replay_is_ordered_bounded_and_scoped(database, worker_config):
    run = await seed_run(database, worker_config)
    other = await seed_run(database, worker_config)
    log = SqlAlchemyEventLog(database)
    for _ in range(5):
        await log.append(draft(run.id))
    await log.append(draft(other.id))

    everything = await log.replay(run.id)
    resumed = await log.replay(run.id, after_seq=3)
    capped = await log.replay(run.id, limit=2)

    assert [event.seq for event in everything] == [1, 2, 3, 4, 5]
    assert [event.seq for event in resumed] == [4, 5]
    assert [event.seq for event in capped] == [1, 2]
    assert all(event.run_id == run.id for event in everything)


async def test_a_payload_survives_the_round_trip(database, worker_config):
    """What the client is sent on a reconnect has to be what it was sent live."""
    run = await seed_run(database, worker_config)
    log = SqlAlchemyEventLog(database)
    payload = {
        "research_goal": "Compare inference providers",
        "iteration": 2,
        "tasks": [{"external_id": "pricing", "question": "What does it cost?"}],
    }

    live = await log.append(
        draft(run.id, type=ResearchEventType.PLANNER_COMPLETED, payload=payload)
    )
    replayed = (await log.replay(run.id))[0]

    assert replayed.payload == payload
    # Frames compared as parsed events, not as bytes: `jsonb` stores an object
    # rather than the text of one, so a replayed frame's keys come back in
    # Postgres' order. That is the column doing its job - the contract is the
    # event a client parses, and a client that depended on key order would be
    # broken by any JSON library.
    assert _frame_data(replayed) == _frame_data(live)
    assert replayed.seq == live.seq and replayed.type is live.type


async def test_an_event_for_a_run_that_does_not_exist_is_refused(database, worker_config):
    """A foreign key violation is not contention, so it is not retried into one."""
    log = SqlAlchemyEventLog(database)

    with pytest.raises(Exception, match=r"(?i)foreign key"):
        await log.append(draft(uuid.uuid4()))


def _frame_data(event: ResearchEvent) -> dict[str, Any]:
    """The ``data:`` line of an SSE frame, parsed."""
    body = event.to_sse_frame().split("data: ", 1)[1].strip()
    return dict(json.loads(body))


# --- the broker over it ---------------------------------------------------


async def test_replay_survives_a_transport_that_remembers_nothing(database, worker_config):
    """A second process, with its own empty transport, still replays the run.

    This is what the durable log buys: the client that reconnects to another
    replica is not served an empty history.
    """
    run = await seed_run(database, worker_config)
    log = SqlAlchemyEventLog(database)
    publisher = DurableEventBroker(transport=InMemoryEventBroker(), log=log)
    await publisher.publish(draft(run.id))
    await publisher.publish(draft(run.id, type=ResearchEventType.CRITIC_STARTED))

    elsewhere = DurableEventBroker(transport=InMemoryEventBroker(), log=log)
    replayed = await elsewhere.history(run.id)

    assert [event.type for event in replayed] == [
        ResearchEventType.SOURCE_FOUND,
        ResearchEventType.CRITIC_STARTED,
    ]


async def test_a_delivery_failure_does_not_lose_the_event(database, worker_config):
    """Store, then deliver. Only the second of those is allowed to fail."""
    run = await seed_run(database, worker_config)
    log = SqlAlchemyEventLog(database)
    broker = DurableEventBroker(transport=_BrokenTransport(), log=log)

    event = await broker.publish(draft(run.id))

    assert event.seq == 1
    assert [stored.seq for stored in await log.replay(run.id)] == [1]


async def test_the_test_environment_still_gets_a_durable_broker(database, worker_config):
    """`APP_ENV=test` swaps the transport, not the record.

    Worth pinning: a suite that quietly dropped persistence would test a
    configuration no deployment runs.
    """
    broker = build_event_broker(worker_config, database=database)

    assert isinstance(broker, DurableEventBroker)


async def test_persistence_can_be_turned_off_on_purpose(database, worker_config):
    broker = build_event_broker(
        worker_config.model_copy(update={"persist_research_events": False}),
        database=database,
    )

    assert isinstance(broker, InMemoryEventBroker)


class _BrokenTransport:
    """A transport that is down. Everything else about the system is not."""

    async def publish(self, draft: EventDraft) -> ResearchEvent:  # pragma: no cover
        raise AssertionError("the durable broker numbers events itself")

    async def relay(self, event: ResearchEvent) -> None:
        raise ConnectionError("redis is unreachable")

    async def history(self, run_id: uuid.UUID, *, after_seq: int = 0) -> list[ResearchEvent]:
        return []

    def subscribe(self, run_id: uuid.UUID) -> Any:  # pragma: no cover - not exercised
        raise NotImplementedError

    async def close(self) -> None:
        return None
