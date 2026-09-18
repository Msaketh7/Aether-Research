"""The gateway's concurrency limit, made observable (Phase 21).

The gateway has always held a semaphore: fifty parallel researchers become
``llm_max_concurrent_calls`` active calls and the rest queue. That is the
system's own throttle, and until now it was invisible - a run that spent nine
seconds waiting for a slot and one second in the model looked, in every record
this repository keeps, exactly like a run that spent one second in a slow
model. ``latency_ms`` on a successful call is the provider's own measure and
starts after the slot is held, which is correct and is precisely why the wait
had to be counted somewhere else.

So the semaphore is wrapped rather than replaced. Same acquire, same release,
same fairness; what is added is the clock either side of the acquire and the
counters a load test reads afterwards. The cost is one ``loop.time()`` per
call, which is noise beside a network round trip.

**Nothing here fails a call.** The observer is invoked inside a guard for the
same reason ``app.observability.instruments`` guards every metric: a histogram
must not take down a research run.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType

from app.core.logging import get_logger

logger = get_logger(__name__)

#: Called with the seconds a call waited for a slot. Zero is the common case
#: and is reported: "never waited" and "waited 0.4s on one call in a hundred"
#: are different systems, and a histogram that only saw the waits could not
#: tell them apart.
SlotObserver = Callable[[float], None]


@dataclass(frozen=True, slots=True)
class Saturation:
    """What the limiter has seen since the process started.

    A snapshot, not a live view: read it twice and subtract to get a window.
    ``max_in_flight`` reaching ``limit`` is the statement that the ceiling
    bound something; ``waited`` above zero is the statement that it cost
    somebody time.
    """

    limit: int
    in_flight: int
    waiting: int
    acquisitions: int
    #: Acquisitions that did not get a slot immediately.
    waited: int
    total_wait_seconds: float
    max_wait_seconds: float
    max_in_flight: int

    @property
    def mean_wait_seconds(self) -> float:
        """Averaged over *every* acquisition, not only the ones that waited."""
        return self.total_wait_seconds / self.acquisitions if self.acquisitions else 0.0

    @property
    def utilisation(self) -> float:
        """The high-water mark as a fraction of the ceiling."""
        return self.max_in_flight / self.limit if self.limit else 0.0


class ConcurrencyLimiter:
    """An ``asyncio.Semaphore`` that says how much it cost to get through it."""

    def __init__(self, limit: int, *, observer: SlotObserver | None = None) -> None:
        if limit < 1:
            raise ValueError("A concurrency limit below one would admit nothing.")
        self._limit = limit
        self._semaphore = asyncio.Semaphore(limit)
        self._observer = observer
        self._in_flight = 0
        self._waiting = 0
        self._acquisitions = 0
        self._waited = 0
        self._total_wait = 0.0
        self._max_wait = 0.0
        self._max_in_flight = 0

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def in_flight(self) -> int:
        """Calls holding a slot right now. Read at scrape time by a gauge."""
        return self._in_flight

    @property
    def saturation(self) -> Saturation:
        return Saturation(
            limit=self._limit,
            in_flight=self._in_flight,
            waiting=self._waiting,
            acquisitions=self._acquisitions,
            waited=self._waited,
            total_wait_seconds=self._total_wait,
            max_wait_seconds=self._max_wait,
            max_in_flight=self._max_in_flight,
        )

    def slot(self) -> Slot:
        """A context manager holding one slot for the duration of its block.

        A fresh object per acquisition rather than ``async with limiter``,
        because an ``__aenter__`` on the limiter itself would have to keep the
        start time of the acquire in progress on the shared instance - and
        there are eight of those in flight by design.
        """
        return Slot(self)

    # --- what a slot does to the limiter ----------------------------------

    async def acquire(self) -> None:
        loop = asyncio.get_running_loop()
        # `locked()` is checked before awaiting so that the common case - a
        # free slot - is recorded as a zero wait rather than as however long
        # the event loop took to come back to us, which would measure the
        # scheduler and call it throttling.
        contended = self._semaphore.locked()
        started = loop.time() if contended else 0.0
        if contended:
            self._waiting += 1
        try:
            await self._semaphore.acquire()
        finally:
            if contended:
                self._waiting -= 1
        waited = loop.time() - started if contended else 0.0

        self._in_flight += 1
        self._max_in_flight = max(self._max_in_flight, self._in_flight)
        self._acquisitions += 1
        self._total_wait += waited
        if contended:
            self._waited += 1
            self._max_wait = max(self._max_wait, waited)
        self._observe(waited)

    def release(self) -> None:
        self._in_flight -= 1
        self._semaphore.release()

    def _observe(self, waited: float) -> None:
        if self._observer is None:
            return
        try:
            self._observer(waited)
        except Exception:  # pragma: no cover - an observer must not fail a call
            logger.debug("a slot observer raised", extra={"waited_seconds": waited})


class Slot:
    """One acquisition, so that two can be open on the same limiter at once."""

    __slots__ = ("_limiter",)

    def __init__(self, limiter: ConcurrencyLimiter) -> None:
        self._limiter = limiter

    async def __aenter__(self) -> None:
        await self._limiter.acquire()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._limiter.release()
