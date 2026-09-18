"""Research progress events and the broker that relays them (ADR 0006).

The worker publishes; the API relays to whichever client holds the SSE
connection. Three properties the frontend depends on are guaranteed here:

* ``seq`` is monotonic per run, so a client reconnecting with ``Last-Event-ID``
  is replayed from exactly where it stopped rather than from the beginning;
* every event carries the run ``status``, so the UI never needs a second
  request to keep its header correct;
* a terminal event is the last one, so the stream can be closed rather than
  held open on a run that will never change again.

**A caller hands over a draft and gets back a numbered event.** Numbering is
not something a publisher does for itself: the number has to be unique per run
across every process that emits, and the only component that can promise that
is the one that stores the event. Phase 2 had ``next_seq`` and ``publish`` as
two calls, which is a sequence allocated in one place and used in another - and
that is exactly the shape that produces a duplicate ``id:`` the first time two
processes emit for one run. ``publish`` now does both, and ``relay`` is what a
broker that numbers elsewhere uses to hand an already-numbered event to a
transport.

Phase 2 shipped the in-memory broker, which is correct for a single API
process. Phase 14 adds the Redis pub/sub transport and the durable log behind
the same interface (``app.research.eventbus``), which is what makes multiple
API replicas - and a worker in a different process - possible.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict, deque
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import ResearchEventType, RunStatus

__all__ = [
    "EventBroker",
    "EventDraft",
    "InMemoryEventBroker",
    "ResearchEvent",
    "ResearchEventType",
]


class EventDraft(BaseModel):
    """An event before it has a number. What a publisher constructs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: ResearchEventType
    run_id: UUID
    status: RunStatus
    payload: dict[str, Any] = Field(default_factory=dict)


class ResearchEvent(BaseModel):
    """One progress event. Serialised as the ``data:`` field of an SSE frame."""

    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=1)
    type: ResearchEventType
    run_id: UUID
    at: datetime
    status: RunStatus
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def of(cls, draft: EventDraft, *, seq: int, at: datetime | None = None) -> ResearchEvent:
        """Number a draft. ``at`` is the store's timestamp where there is one."""
        return cls(
            seq=seq,
            type=draft.type,
            run_id=draft.run_id,
            at=at or datetime.now(UTC),
            status=draft.status,
            payload=dict(draft.payload),
        )

    def to_sse_frame(self) -> str:
        """Render as a named SSE event.

        ``id:`` is the sequence number, which is what the browser echoes back in
        ``Last-Event-ID``; ``event:`` is the type, so the client subscribes per
        type rather than switching on a field.
        """
        data = self.model_dump_json()
        return f"id: {self.seq}\nevent: {self.type.value}\ndata: {data}\n\n"


class EventBroker(Protocol):
    """What the API and the worker need from the progress bus."""

    async def publish(self, draft: EventDraft) -> ResearchEvent:
        """Number the draft, record it, deliver it, and return what was sent."""
        ...

    async def relay(self, event: ResearchEvent) -> None:
        """Deliver an event that already has its number.

        Used by a broker that numbers events somewhere else - the durable one
        numbers them in Postgres - to hand the result to its transport. A
        caller emitting progress uses ``publish``.
        """
        ...

    async def history(self, run_id: UUID, *, after_seq: int = 0) -> list[ResearchEvent]:
        """Events after ``after_seq``, oldest first, for reconnect replay."""
        ...

    def subscribe(self, run_id: UUID) -> AsyncGenerator[ResearchEvent, None]:
        """Live events for one run.

        A generator rather than a bare iterator so the caller can ``aclose()``
        it and guarantee the subscription is removed even if the client
        disconnects mid-stream.
        """
        ...

    async def close(self) -> None: ...


class InMemoryEventBroker:
    """Single-process broker.

    Correct while the API runs as one process and nothing else emits, which is
    the Phase 2 topology and the shape of a test. A bounded buffer per run caps
    memory: a long deep run can emit thousands of events, and holding all of
    them forever would be a slow leak.
    """

    def __init__(self, buffer_size: int = 500) -> None:
        self._buffer_size = buffer_size
        self._buffers: dict[UUID, deque[ResearchEvent]] = defaultdict(
            lambda: deque(maxlen=buffer_size)
        )
        self._subscribers: dict[UUID, list[asyncio.Queue[ResearchEvent]]] = defaultdict(list)
        self._sequences: dict[UUID, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def publish(self, draft: EventDraft) -> ResearchEvent:
        async with self._lock:
            self._sequences[draft.run_id] += 1
            event = ResearchEvent.of(draft, seq=self._sequences[draft.run_id])
        await self.relay(event)
        return event

    async def relay(self, event: ResearchEvent) -> None:
        async with self._lock:
            self._buffers[event.run_id].append(event)
            # A relayed event was numbered elsewhere; keep the local counter
            # ahead of it so a later publish on this broker cannot reuse a
            # number that has already been sent.
            self._sequences[event.run_id] = max(self._sequences[event.run_id], event.seq)
            subscribers = list(self._subscribers[event.run_id])

        for queue in subscribers:
            # Drop the event for one slow subscriber rather than blocking the
            # publisher for everyone; that client catches up from the buffer
            # when it reconnects with Last-Event-ID.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    async def history(self, run_id: UUID, *, after_seq: int = 0) -> list[ResearchEvent]:
        async with self._lock:
            return [event for event in self._buffers[run_id] if event.seq > after_seq]

    async def subscribe(self, run_id: UUID) -> AsyncGenerator[ResearchEvent, None]:
        queue: asyncio.Queue[ResearchEvent] = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers[run_id].append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            async with self._lock:
                if queue in self._subscribers[run_id]:
                    self._subscribers[run_id].remove(queue)

    async def close(self) -> None:
        async with self._lock:
            self._buffers.clear()
            self._subscribers.clear()
            self._sequences.clear()
