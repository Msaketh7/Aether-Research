"""The progress bus across processes: Redis for fan-out, Postgres for replay.

Phase 2 shipped an in-memory broker, which is right for one process and wrong
for two. Phase 13 put the graph in a worker, so the process that knows what is
happening is no longer the process holding the client's connection. This is
what closes that gap, and it is deliberately two objects rather than one:

* ``RedisEventBroker`` is **transport**. A pub/sub channel per run fans an
  event out to whichever API replica happens to be holding the stream, and a
  capped list beside it answers a reconnect from cache. Both keys expire: a
  finished run's events are not Redis' job to keep.
* ``DurableEventBroker`` is **the record**. It numbers each event by storing
  it (``app.db.repositories.events``) and only then hands it to the transport,
  so the number a client sees in ``id:`` is the number Postgres assigned. Its
  ``history`` reads rows, which is why replay survives an evicted key, a Redis
  restart, and a reconnect an hour into a run.

Ordering is "store, then deliver", and that direction is chosen: an event
delivered but not stored is one a reconnecting client will never be told about
again, while an event stored but not delivered is one the next reconnect
replays. Only the second failure is recoverable, so it is the one to have.

**Redis being down degrades the stream; it does not fail the run.** A publish
whose fan-out throws is logged and swallowed, because the event is already
durable and the worker's job is the research, not the telemetry. A publish
whose *store* throws is raised, because losing the record is the failure this
module exists to prevent.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import AsyncGenerator
from typing import Protocol
from uuid import UUID

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.logging import get_logger
from app.db.session import Database
from app.research.events import EventBroker, EventDraft, InMemoryEventBroker, ResearchEvent

logger = get_logger(__name__)

#: Key namespace. One channel and one buffer per run, both derived from the id
#: so nothing has to be looked up to publish.
CHANNEL_PREFIX = "run:events:"
BUFFER_PREFIX = "run:events:buffer:"
SEQUENCE_PREFIX = "run:events:seq:"


def channel_for(run_id: UUID) -> str:
    return f"{CHANNEL_PREFIX}{run_id}"


class EventLog(Protocol):
    """The durable record of what was streamed (ADR 0006)."""

    async def append(self, draft: EventDraft) -> ResearchEvent:
        """Number the event and store it. The number comes from the insert."""
        ...

    async def replay(
        self, run_id: UUID, *, after_seq: int = 0, limit: int | None = None
    ) -> list[ResearchEvent]: ...

    async def last_seq(self, run_id: UUID) -> int: ...


class RedisEventBroker:
    """Cross-replica fan-out, with a capped buffer for a fast reconnect.

    Pub/sub rather than a stream: a progress event has no value to a subscriber
    that is not connected, and the durable log already answers "what did I
    miss". A Redis Stream would add consumer groups and trimming policy to
    solve a problem that is solved one layer down.
    """

    def __init__(
        self,
        redis: Redis,
        *,
        buffer_size: int = 500,
        ttl_seconds: int = 3600,
    ) -> None:
        self._redis = redis
        self._buffer_size = buffer_size
        self._ttl = ttl_seconds

    async def publish(self, draft: EventDraft) -> ResearchEvent:
        """Number from Redis' own counter, then deliver.

        Used when there is no durable log - a deployment that has turned
        persistence off, and the tests that exercise the transport by itself.
        ``INCR`` is atomic across replicas, so the numbers are unique for as
        long as the key lives; the counter shares the buffer's expiry, which is
        why the durable broker does not rely on it.
        """
        key = f"{SEQUENCE_PREFIX}{draft.run_id}"
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, self._ttl)
            seq = int((await pipe.execute())[0])
        event = ResearchEvent.of(draft, seq=seq)
        await self.relay(event)
        return event

    async def relay(self, event: ResearchEvent) -> None:
        """Buffer the event and publish it to the run's channel."""
        frame = event.model_dump_json()
        buffer = f"{BUFFER_PREFIX}{event.run_id}"
        async with self._redis.pipeline(transaction=False) as pipe:
            pipe.rpush(buffer, frame)
            pipe.ltrim(buffer, -self._buffer_size, -1)
            pipe.expire(buffer, self._ttl)
            pipe.publish(channel_for(event.run_id), frame)
            await pipe.execute()

    async def history(self, run_id: UUID, *, after_seq: int = 0) -> list[ResearchEvent]:
        frames = await self._redis.lrange(f"{BUFFER_PREFIX}{run_id}", 0, -1)
        events = [_decode(frame) for frame in frames]
        return sorted(
            (event for event in events if event is not None and event.seq > after_seq),
            key=lambda event: event.seq,
        )

    async def subscribe(self, run_id: UUID) -> AsyncGenerator[ResearchEvent, None]:
        """Live events for one run, until the caller closes the generator."""
        pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        await pubsub.subscribe(channel_for(run_id))
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                event = _decode(message["data"])
                if event is not None:
                    yield event
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(channel_for(run_id))
            with contextlib.suppress(Exception):
                # The client library ships no annotation for this one; the
                # connection is released here or it is leaked per subscriber.
                await pubsub.aclose()  # type: ignore[no-untyped-call]

    async def close(self) -> None:
        await self._redis.aclose()


class DurableEventBroker:
    """Numbers and stores every event, then hands it to a transport."""

    def __init__(self, *, transport: EventBroker, log: EventLog) -> None:
        self._transport = transport
        self._log = log

    async def publish(self, draft: EventDraft) -> ResearchEvent:
        event = await self._log.append(draft)
        try:
            await self._transport.relay(event)
        except Exception as exc:
            # The event is durable, so a client learns about it on its next
            # reconnect. Failing the caller here would turn a telemetry outage
            # into a failed research run.
            logger.warning(
                "a progress event was stored but could not be delivered",
                extra={
                    "run_id": str(event.run_id),
                    "seq": event.seq,
                    "type": event.type.value,
                    "error": str(exc),
                },
            )
        return event

    async def relay(self, event: ResearchEvent) -> None:
        await self._transport.relay(event)

    async def history(self, run_id: UUID, *, after_seq: int = 0) -> list[ResearchEvent]:
        """Replay from the rows, falling back to the transport's buffer.

        The fallback is not redundancy for its own sake: a database that is
        briefly unavailable should cost a client the oldest part of its replay,
        not the connection.
        """
        try:
            return await self._log.replay(run_id, after_seq=after_seq)
        except Exception as exc:
            logger.warning(
                "replaying a run's events from the database failed",
                extra={"run_id": str(run_id), "error": str(exc)},
            )
            return await self._transport.history(run_id, after_seq=after_seq)

    def subscribe(self, run_id: UUID) -> AsyncGenerator[ResearchEvent, None]:
        return self._transport.subscribe(run_id)

    async def close(self) -> None:
        await self._transport.close()


def build_event_broker(settings: Settings, *, database: Database) -> EventBroker:
    """The broker this process should use.

    Redis everywhere except tests, chosen exactly as the queue is chosen and
    for the same reason: a deployment that silently fell back to an in-process
    bus would stream progress to whichever replica happened to run the API, and
    to nobody at all when the worker is the one emitting.

    Persistence is on unless it is turned off, and turning it off is a real
    choice rather than an accident: replay then comes from the Redis buffer,
    which is bounded and expires.
    """
    transport: EventBroker
    if settings.app_env == "test":
        transport = InMemoryEventBroker(buffer_size=settings.sse_replay_buffer_size)
    else:
        from app.workers.queue import build_redis

        transport = RedisEventBroker(
            build_redis(settings.redis_url),
            buffer_size=settings.sse_replay_buffer_size,
            ttl_seconds=settings.sse_event_buffer_ttl_seconds,
        )

    if not settings.persist_research_events:
        return transport

    from app.db.repositories.events import SqlAlchemyEventLog

    return DurableEventBroker(transport=transport, log=SqlAlchemyEventLog(database))


def _decode(frame: str | bytes) -> ResearchEvent | None:
    """Parse a published frame, or report that it could not be parsed.

    Returning ``None`` rather than raising: a frame this cannot read is one
    event, and killing the subscription would cost the client every event
    after it too.
    """
    try:
        return ResearchEvent.model_validate(json.loads(frame))
    except Exception:
        logger.warning("discarded an unreadable progress event frame")
        return None


__all__ = [
    "DurableEventBroker",
    "EventLog",
    "RedisEventBroker",
    "build_event_broker",
    "channel_for",
]
