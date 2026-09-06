"""The API-to-worker handoff (ADR 0005).

The API never runs a research graph. It validates, persists, and pushes a job
here; a worker process picks it up. That boundary is the single most important
structural decision in the system, so it is an explicit interface rather than a
function call that someone could later "optimise" into an inline await.

Job payloads carry only a ``run_id``. All state lives in the database, so a
redelivered job is idempotent and a worker restart loses nothing.
"""

from __future__ import annotations

import json
from typing import Protocol
from uuid import UUID

from redis.asyncio import Redis

from app.core.logging import get_logger

logger = get_logger(__name__)

RESEARCH_QUEUE = "queue:research"


class JobQueue(Protocol):
    """What the API needs from a queue, and nothing more."""

    async def enqueue(self, run_id: UUID) -> None: ...

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

    async def enqueue(self, run_id: UUID) -> None:
        self.jobs.append(run_id)
        logger.info("research run enqueued (in-memory)", extra={"run_id": str(run_id)})

    async def depth(self) -> int:
        return len(self.jobs)

    async def check(self) -> bool:
        return True

    async def close(self) -> None:
        self.jobs.clear()


def build_redis(url: str) -> Redis:
    """A Redis client with timeouts. An unbounded client can hang a request."""
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=3,
        health_check_interval=30,
    )
