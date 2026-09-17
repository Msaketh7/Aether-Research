"""The queue on both sides of the handoff: what the API pushes, what a worker takes.

The in-memory adapter is what the test environment selects and what every worker
test runs on, so its blocking behaviour has to match the Redis one rather than
merely satisfy the same signature.

The Redis tests need a server. CI has one; a developer machine may not, and they
skip with a reason rather than passing silently - the same arrangement the
pgvector tests use. Nothing here fakes a Redis: a queue whose blocking pop and
timeout were simulated would prove nothing about the only part that is subtle.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from redis.asyncio import Redis

from app.core.config import Settings
from app.workers.queue import InMemoryJobQueue, RedisJobQueue, build_redis
from app.workers.runner import build_queue

SKIP_REASON = "no Redis server on REDIS_URL; CI runs these against a real one"


@pytest.fixture
async def redis_queue(settings: Settings):
    """A queue on a key of its own, so a real developer Redis is not disturbed."""
    client: Redis = build_redis(settings.redis_url, socket_timeout=6.0)
    try:
        await client.ping()
    except Exception:
        await client.aclose()
        pytest.skip(SKIP_REASON)

    name = f"queue:test:{uuid.uuid4().hex}"
    queue = RedisJobQueue(client, queue_name=name)
    try:
        yield queue
    finally:
        await client.delete(name)
        await queue.close()


# --- the in-memory adapter ------------------------------------------------------


async def test_jobs_are_taken_in_the_order_they_were_queued():
    queue = InMemoryJobQueue()
    first, second = uuid.uuid4(), uuid.uuid4()
    await queue.enqueue(first)
    await queue.enqueue(second)

    assert await queue.reserve(timeout_seconds=0.1) == first
    assert await queue.reserve(timeout_seconds=0.1) == second
    assert await queue.depth() == 0


async def test_reserving_from_an_empty_queue_waits_and_then_gives_up():
    """It has to wait rather than return at once: the worker's loop calls this
    in a tight cycle, and a reserve that never blocks is a spin."""
    queue = InMemoryJobQueue()
    started = time.monotonic()

    assert await queue.reserve(timeout_seconds=0.2) is None

    assert time.monotonic() - started >= 0.15


async def test_a_job_queued_while_a_worker_waits_wakes_it():
    queue = InMemoryJobQueue()
    run_id = uuid.uuid4()

    async def enqueue_shortly() -> None:
        await asyncio.sleep(0.05)
        await queue.enqueue(run_id)

    reserved, _ = await asyncio.gather(queue.reserve(timeout_seconds=5.0), enqueue_shortly())

    assert reserved == run_id


# --- Redis ----------------------------------------------------------------------


async def test_a_job_pushed_by_the_api_is_taken_by_a_worker(redis_queue: RedisJobQueue):
    run_id = uuid.uuid4()
    await redis_queue.enqueue(run_id)

    assert await redis_queue.depth() == 1
    assert await redis_queue.reserve(timeout_seconds=1.0) == run_id
    assert await redis_queue.depth() == 0


async def test_a_quiet_queue_is_not_mistaken_for_a_broken_one(redis_queue: RedisJobQueue):
    """The client's socket timeout has to outlast the blocking pop. When it did
    not, every idle poll raised a read timeout that looked exactly like an
    outage - which is why ``build_redis`` takes the timeout as a parameter."""
    started = time.monotonic()

    assert await redis_queue.reserve(timeout_seconds=1.0) is None

    assert time.monotonic() - started >= 0.9


async def test_one_job_goes_to_exactly_one_of_two_waiting_workers(redis_queue: RedisJobQueue):
    """The pop is atomic, so two workers do not both get the same run - and the
    claim in Postgres is the second line of defence, not the first."""
    await redis_queue.enqueue(uuid.uuid4())

    taken = await asyncio.gather(
        redis_queue.reserve(timeout_seconds=2.0),
        redis_queue.reserve(timeout_seconds=2.0),
    )

    assert sorted(value is None for value in taken) == [False, True]


# --- selection ------------------------------------------------------------------


def test_a_worker_outside_tests_is_given_the_redis_queue(settings: Settings):
    """The in-memory queue consumes only what the same process enqueued, which
    for a worker is nothing. It must never be selected by omission."""
    assert isinstance(build_queue(settings), InMemoryJobQueue)
    assert isinstance(
        build_queue(settings.model_copy(update={"app_env": "production"})), RedisJobQueue
    )
