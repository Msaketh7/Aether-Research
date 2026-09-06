"""Research progress events and the broker that relays them (ADR 0006).

The worker publishes; the API relays to whichever client holds the SSE
connection. Two properties the frontend already depends on are guaranteed here:

* ``seq`` is monotonic per run, so a client reconnecting with ``Last-Event-ID``
  is replayed from exactly where it stopped rather than from the beginning;
* every event carries the run ``status``, so the UI never needs a second
  request to keep its header correct.

Phase 2 ships the in-memory broker, which is correct for a single API process.
Phase 14 adds the Redis pub/sub implementation behind the same interface, which
is what makes multiple API replicas possible.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict, deque
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import RunStatus


class ResearchEventType(StrEnum):
    """Mirrors ``RESEARCH_EVENT_TYPES`` in ``@aether/shared-types``."""

    RESEARCH_STARTED = "research_started"
    PLANNER_STARTED = "planner_started"
    PLANNER_COMPLETED = "planner_completed"
    SUBTASK_STARTED = "subtask_started"
    SEARCH_STARTED = "search_started"
    SOURCE_FOUND = "source_found"
    SOURCE_PROCESSED = "source_processed"
    SOURCES_PROGRESS = "sources_progress"
    CLAIM_EXTRACTED = "claim_extracted"
    EVIDENCE_PROGRESS = "evidence_progress"
    VERIFICATION_STARTED = "verification_started"
    CONTRADICTION_FOUND = "contradiction_found"
    CRITIC_STARTED = "critic_started"
    ADDITIONAL_RESEARCH_REQUESTED = "additional_research_requested"
    ITERATION_STARTED = "iteration_started"
    SYNTHESIS_STARTED = "synthesis_started"
    CITATION_CHECK = "citation_check"
    REPORT_COMPLETED = "report_completed"
    RESEARCH_FAILED = "research_failed"
    RESEARCH_CANCELLED = "research_cancelled"

    @property
    def is_terminal(self) -> bool:
        """After a terminal event the stream is closed deliberately."""
        return self in _TERMINAL_EVENTS


_TERMINAL_EVENTS = frozenset(
    {
        ResearchEventType.REPORT_COMPLETED,
        ResearchEventType.RESEARCH_FAILED,
        ResearchEventType.RESEARCH_CANCELLED,
    }
)


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
    def build(
        cls,
        *,
        seq: int,
        type: ResearchEventType,
        run_id: UUID,
        status: RunStatus,
        payload: dict[str, Any] | None = None,
    ) -> ResearchEvent:
        return cls(
            seq=seq,
            type=type,
            run_id=run_id,
            at=datetime.now(UTC),
            status=status,
            payload=payload or {},
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

    async def publish(self, event: ResearchEvent) -> None: ...

    async def history(self, run_id: UUID, *, after_seq: int = 0) -> list[ResearchEvent]:
        """Buffered events after ``after_seq``, for reconnect replay."""
        ...

    def subscribe(self, run_id: UUID) -> AsyncGenerator[ResearchEvent, None]:
        """Live events for one run.

        A generator rather than a bare iterator so the caller can ``aclose()``
        it and guarantee the subscription is removed even if the client
        disconnects mid-stream.
        """
        ...

    async def next_seq(self, run_id: UUID) -> int:
        """Allocate the next sequence number for a run."""
        ...


class InMemoryEventBroker:
    """Single-process broker.

    Correct while the API runs as one process, which is the Phase 2 topology.
    A bounded buffer per run caps memory: a long deep run can emit thousands of
    events, and holding all of them forever would be a slow leak.
    """

    def __init__(self, buffer_size: int = 500) -> None:
        self._buffer_size = buffer_size
        self._buffers: dict[UUID, deque[ResearchEvent]] = defaultdict(
            lambda: deque(maxlen=buffer_size)
        )
        self._subscribers: dict[UUID, list[asyncio.Queue[ResearchEvent]]] = defaultdict(list)
        self._sequences: dict[UUID, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def next_seq(self, run_id: UUID) -> int:
        async with self._lock:
            self._sequences[run_id] += 1
            return self._sequences[run_id]

    async def publish(self, event: ResearchEvent) -> None:
        async with self._lock:
            self._buffers[event.run_id].append(event)
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
