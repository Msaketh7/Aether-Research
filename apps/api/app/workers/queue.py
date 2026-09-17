"""The API-to-worker handoff (ADR 0005).

The API never runs a research graph. It validates, persists, and pushes a job
here; a worker process picks it up. That boundary is the single most important
structural decision in the system, so it is an explicit interface rather than a
function call that someone could later "optimise" into an inline await.

Job payloads carry only a ``run_id``. All state lives in the database, so a
redelivered job is idempotent and a worker restart loses nothing.

**A reserved job is not acknowledged, and does not need to be.** ``reserve``
removes the id from the list, so a worker killed a microsecond later loses the
message - and the run is still ``queued`` in Postgres, where the reconciliation
sweep finds it (ADR 0005). Adding an in-flight list and a visibility timeout
would put a second, weaker copy of that answer in Redis, and the two would
disagree the first time Redis restarted.
"""

from __future__ import annotations

import asyncio
import json
from typing import Protocol
from uuid import UUID

from redis.asyncio import Redis

from app.core.logging import get_logger

logger = get_logger(__name__)

RESEARCH_QUEUE = "queue:research"


class JobQueue(Protocol):
    """What the API needs from a queue, and what a worker needs to consume it."""

    async def enqueue(self, run_id: UUID) -> None: ...

    async def reserve(self, *, timeout_seconds: float) -> UUID | None:
        """The next run to execute, or ``None`` if none arrived in time.

        Blocks rather than polling, and returns on a timeout so the worker's
        loop can tick - reconcile, and notice that it has been asked to stop.
        """
        ...

    async def depth(self) -> int:
        """Pending jobs. Drives worker autoscaling and the readiness report."""
        ...

    async def check(self) -> bool:
        """Readiness probe. False rather than raising."""
        ...

    async def close(self) -> None: ...


class RedisJobQueue:
    """Redis list used as a FIFO queue.

    A list is enough for the handoff: the queue is a *dispatch* mechanism, not
    the source of truth. A job lost to a Redis restart is recovered by the
    reconciliation sweep over runs stuck in `queued` (ADR 0005), which is also
    what makes at-least-once delivery acceptable here.
    """

    def __init__(self, redis: Redis, queue_name: str = RESEARCH_QUEUE) -> None:
        self._redis = redis
        self._queue = queue_name

    async def enqueue(self, run_id: UUID) -> None:
        payload = json.dumps({"run_id": str(run_id)})
        await self._redis.rpush(self._queue, payload)
        logger.info("research run enqueued", extra={"run_id": str(run_id)})

    async def reserve(self, *, timeout_seconds: float) -> UUID | None:
        """``BLPOP`` with a timeout, so the worker sleeps rather than polls.

        The client's socket timeout has to outlast this, or every quiet poll
        raises a read timeout that is indistinguishable from a broken server.
        ``build_redis`` takes the timeout for exactly that reason, and the
        worker sizes it from its poll interval.
        """
        reply = await self._redis.blpop([self._queue], timeout=timeout_seconds)
        if reply is None:
            return None
        return _decode_job(reply[1])

    async def depth(self) -> int:
        return int(await self._redis.llen(self._queue))

    async def check(self) -> bool:
        try:
            await self._redis.ping()
        except Exception as exc:
            logger.warning("redis health check failed", extra={"error": str(exc)})
            return False
        return True

    async def close(self) -> None:
        await self._redis.aclose()


class InMemoryJobQueue:
    """In-process queue for tests and for running the API without Redis.

    Not a fallback in production: `app.state` wires this in only when the
    environment is `test`, so a misconfigured deployment fails its readiness
    probe instead of silently dropping research jobs on the floor.
    """

    def __init__(self) -> None:
        self.jobs: list[UUID] = []
        self._arrived = asyncio.Event()

    async def enqueue(self, run_id: UUID) -> None:
        self.jobs.append(run_id)
        self._arrived.set()
        logger.info("research run enqueued (in-memory)", extra={"run_id": str(run_id)})

    async def reserve(self, *, timeout_seconds: float) -> UUID | None:
        """Waits like the Redis one does, so a worker test exercises the wait."""
        if not self.jobs:
            self._arrived.clear()
            try:
                async with asyncio.timeout(timeout_seconds):
                    await self._arrived.wait()
            except TimeoutError:
                return None
        if not self.jobs:
            # Another consumer took it between the wake-up and here.
            return None
        return self.jobs.pop(0)

    async def depth(self) -> int:
        return len(self.jobs)

    async def check(self) -> bool:
        return True

    async def close(self) -> None:
        self.jobs.clear()
        self._arrived.set()


def _decode_job(payload: str | bytes) -> UUID:
    """The run id inside a queued payload.

    A payload that is not one is a programming error rather than an attack
    surface: only this module writes to the queue. It still fails loudly, since
    silently dropping a job would lose a run until the sweep noticed.
    """
    if isinstance(payload, bytes):
        payload = payload.decode()
    return UUID(json.loads(payload)["run_id"])


def build_redis(url: str, *, socket_timeout: float = 5.0) -> Redis:
    """A Redis client with timeouts. An unbounded client can hang a request.

    ``socket_timeout`` is a parameter because a worker's blocking reserve has to
    outlive its own BLPOP timeout; the API's short default would turn every
    quiet poll into a read timeout.
    """
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=socket_timeout,
        socket_connect_timeout=3,
        health_check_interval=30,
    )
