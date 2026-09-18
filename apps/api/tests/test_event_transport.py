"""The Redis fan-out behind the progress stream (Phase 14).

These need a real server, and skip with a reason when there is none - the same
arrangement as the queue's Redis tests and the pgvector tests. Nothing here
fakes a pub/sub: the only interesting properties are that a message published
by one connection reaches a subscriber on another, and that the buffer is
capped and expires, and a fake would assert neither.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from redis.asyncio import Redis

from app.core.config import Settings
from app.core.enums import ResearchEventType, RunStatus
from app.research.eventbus import BUFFER_PREFIX, SEQUENCE_PREFIX, RedisEventBroker
from app.research.events import EventDraft, InMemoryEventBroker, ResearchEvent
from app.workers.queue import build_redis

SKIP_REASON = "no Redis server on REDIS_URL; CI runs these against a real one"


def draft(run_id: uuid.UUID, **overrides: object) -> EventDraft:
    fields: dict[str, object] = {
        "type": ResearchEventType.SOURCE_FOUND,
        "run_id": run_id,
        "status": RunStatus.RESEARCHING,
        "payload": {"source_id": str(uuid.uuid4())},
    }
    fields.update(overrides)
    return EventDraft(**fields)  # type: ignore[arg-type]


@pytest.fixture
async def redis_client(settings: Settings):
    client: Redis = build_redis(settings.redis_url)
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        pytest.skip(SKIP_REASON)
    yield client
    await client.aclose()


@pytest.fixture
def run_id() -> uuid.UUID:
    """A fresh id per test, so the keys never collide with a developer's data."""
    return uuid.uuid4()


@pytest.fixture
async def cleaned(redis_client: Redis, run_id: uuid.UUID):
    yield
    await redis_client.delete(f"{BUFFER_PREFIX}{run_id}", f"{SEQUENCE_PREFIX}{run_id}")


# --- the transport --------------------------------------------------------


async def test_a_subscriber_on_another_connection_receives_what_was_published(
    settings: Settings, redis_client: Redis, run_id: uuid.UUID, cleaned: None
):
    """The whole reason this class exists: the emitter and the reader are two
    processes, and an in-process bus reaches neither from the other."""
    publisher = RedisEventBroker(redis_client)
    subscriber = RedisEventBroker(build_redis(settings.redis_url))
    stream = subscriber.subscribe(run_id)

    async def publish_shortly() -> None:
        # The subscription is established by the first `anext`; publishing
        # before that would be a message with nobody listening, which pub/sub
        # drops by design.
        await asyncio.sleep(0.2)
        await publisher.publish(draft(run_id, payload={"source_id": "a"}))

    task = asyncio.create_task(publish_shortly())
    try:
        async with asyncio.timeout(10):
            received = await anext(stream)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await stream.aclose()
        await subscriber.close()

    assert received.type is ResearchEventType.SOURCE_FOUND
    assert received.payload == {"source_id": "a"}
    assert received.seq == 1


async def test_the_counter_is_atomic_across_publishers(
    settings: Settings, redis_client: Redis, run_id: uuid.UUID, cleaned: None
):
    broker = RedisEventBroker(redis_client)

    events = await asyncio.gather(*(broker.publish(draft(run_id)) for _ in range(6)))

    assert sorted(event.seq for event in events) == [1, 2, 3, 4, 5, 6]


async def test_the_buffer_is_capped_and_replays_in_order(
    settings: Settings, redis_client: Redis, run_id: uuid.UUID, cleaned: None
):
    """Bounded on purpose. The durable log is what a long run replays from;
    this is the fast path, and a fast path that grows without limit is a leak."""
    broker = RedisEventBroker(redis_client, buffer_size=3)
    for _ in range(5):
        await broker.publish(draft(run_id))

    buffered = await broker.history(run_id)
    resumed = await broker.history(run_id, after_seq=4)

    assert [event.seq for event in buffered] == [3, 4, 5]
    assert [event.seq for event in resumed] == [5]


async def test_the_keys_expire_so_a_finished_run_is_not_kept_forever(
    settings: Settings, redis_client: Redis, run_id: uuid.UUID, cleaned: None
):
    broker = RedisEventBroker(redis_client, ttl_seconds=120)
    await broker.publish(draft(run_id))

    assert 0 < await redis_client.ttl(f"{BUFFER_PREFIX}{run_id}") <= 120
    assert 0 < await redis_client.ttl(f"{SEQUENCE_PREFIX}{run_id}") <= 120


# --- what the transports have in common -----------------------------------


async def test_the_in_memory_transport_keeps_a_relayed_number(run_id: uuid.UUID):
    """A relayed event was numbered by the durable log, and a later publish on
    the same transport must not reuse a number that has already been sent."""
    broker = InMemoryEventBroker()
    await broker.relay(ResearchEvent.of(draft(run_id), seq=7))

    following = await broker.publish(draft(run_id))

    assert following.seq == 8
    assert [event.seq for event in await broker.history(run_id)] == [7, 8]
