"""Percentiles, and the sampler that watches the levels while a load runs.

Two jobs, both of them arithmetic that is easy to get subtly wrong and then
publish.

**Percentiles are interpolated between order statistics**, which is what
NumPy's default and most dashboards do, and the method is named in the report.
There is more than one defensible definition and they disagree by a whole
sample at small counts - a P99 over ten runs is the maximum under one
definition and the ninth value under another. Ten runs is one of the declared
profiles, so this had to be decided rather than inherited.

**A level is sampled, not integrated.** Queue depth, pool occupancy and the
number of runs executing are instantaneous readings taken at a fixed interval,
so the mean here is the mean *of the samples* - a time average only because
the interval is fixed. The maximum is a maximum of what was seen, which for a
spike shorter than the interval is a maximum that was missed. The interval is
reported next to the numbers so that the reader can tell.
"""

from __future__ import annotations

import asyncio
import contextlib
import statistics
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from types import TracebackType

from app.core.logging import get_logger

logger = get_logger(__name__)


def percentile(values: Sequence[float], fraction: float) -> float:
    """The ``fraction`` quantile, interpolating between order statistics.

    ``fraction`` is in [0, 1]. An empty sequence has no percentile and raises
    rather than returning zero: zero is a latency, and a caller that gets one
    back from no data has been handed a fabricated number.
    """
    if not values:
        raise ValueError("A percentile of nothing is not zero; it does not exist.")
    if not 0.0 <= fraction <= 1.0:
        raise ValueError(f"A percentile fraction must be in [0, 1], not {fraction}.")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1.0 - weight) + ordered[upper] * weight)


@dataclass(frozen=True, slots=True)
class Latency:
    """A distribution, summarised the way the build plan asks for it."""

    count: int
    min: float
    p50: float
    p95: float
    p99: float
    max: float
    mean: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "count": self.count,
            "min": round(self.min, 3),
            "p50": round(self.p50, 3),
            "p95": round(self.p95, 3),
            "p99": round(self.p99, 3),
            "max": round(self.max, 3),
            "mean": round(self.mean, 3),
        }


def summarise(values: Sequence[float]) -> Latency | None:
    """The summary, or ``None`` when there was nothing to summarise.

    ``None`` rather than a row of zeros: a profile in which no run completed
    has no latency distribution, and printing zeros would claim it was fast.
    """
    if not values:
        return None
    return Latency(
        count=len(values),
        min=min(values),
        p50=percentile(values, 0.50),
        p95=percentile(values, 0.95),
        p99=percentile(values, 0.99),
        max=max(values),
        mean=statistics.fmean(values),
    )


@dataclass(frozen=True, slots=True)
class Sample:
    """One reading of every level, taken at ``at`` seconds into the profile."""

    at: float
    #: Jobs waiting on the queue. ``None`` when the queue cannot report a depth.
    queue_depth: int | None
    #: Runs a worker is currently executing.
    running: int
    #: Database pool, as SQLAlchemy counts it.
    db_in_use: int | None
    db_idle: int | None
    db_overflow: int | None
    #: Model calls holding a gateway slot.
    llm_in_flight: int | None


@dataclass(frozen=True, slots=True)
class Series:
    """One level over the whole profile."""

    max: float
    mean: float
    last: float

    def as_dict(self) -> dict[str, float]:
        return {"max": round(self.max, 3), "mean": round(self.mean, 3), "last": round(self.last, 3)}


@dataclass(frozen=True, slots=True)
class Timeline:
    """Every level that was sampled, and how often it was read."""

    samples: int
    interval_seconds: float
    queue_depth: Series | None
    running: Series | None
    db_in_use: Series | None
    db_idle: Series | None
    db_overflow: Series | None
    llm_in_flight: Series | None
    #: The fraction of samples in which every execution slot was busy. The
    #: worker utilisation the build plan asks for, and the honest form of it:
    #: a fraction of *sampled* time, not of wall-clock time.
    saturated_fraction: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "samples": self.samples,
            "interval_seconds": self.interval_seconds,
            "saturated_fraction": (
                None if self.saturated_fraction is None else round(self.saturated_fraction, 3)
            ),
            "levels": {
                name: (None if series is None else series.as_dict())
                for name, series in (
                    ("queue_depth", self.queue_depth),
                    ("running", self.running),
                    ("db_in_use", self.db_in_use),
                    ("db_idle", self.db_idle),
                    ("db_overflow", self.db_overflow),
                    ("llm_in_flight", self.llm_in_flight),
                )
            },
        }


def _series(values: Sequence[float | None]) -> Series | None:
    """A summary of the readings that existed, or ``None`` if none did."""
    present = [float(value) for value in values if value is not None]
    if not present:
        return None
    return Series(max=max(present), mean=statistics.fmean(present), last=present[-1])


def timeline_of(samples: Sequence[Sample], *, interval: float, capacity: int) -> Timeline:
    """Fold the readings into one summary per level."""
    if not samples:
        return Timeline(
            samples=0,
            interval_seconds=interval,
            queue_depth=None,
            running=None,
            db_in_use=None,
            db_idle=None,
            db_overflow=None,
            llm_in_flight=None,
            saturated_fraction=None,
        )
    running = [sample.running for sample in samples]
    return Timeline(
        samples=len(samples),
        interval_seconds=interval,
        queue_depth=_series([sample.queue_depth for sample in samples]),
        running=_series(running),
        db_in_use=_series([sample.db_in_use for sample in samples]),
        db_idle=_series([sample.db_idle for sample in samples]),
        db_overflow=_series([sample.db_overflow for sample in samples]),
        llm_in_flight=_series([sample.llm_in_flight for sample in samples]),
        saturated_fraction=(
            sum(1 for value in running if value >= capacity) / len(running) if capacity else None
        ),
    )


#: What the sampler calls to read the levels. Async because the queue depth is
#: a round trip; everything else it reads is an attribute.
Probe = Callable[[float], Awaitable[Sample]]


@dataclass
class Sampler:
    """Reads the levels on a fixed interval for as long as its block is open.

    A task rather than a callback on the worker loop: the point is to see the
    system while it is busy, and a reading taken by the loop under test is a
    reading that stops arriving exactly when the loop is most occupied - which
    is the moment worth seeing.
    """

    probe: Probe
    interval_seconds: float
    samples: list[Sample] = field(default_factory=list)
    _task: asyncio.Task[None] | None = field(default=None, init=False)
    _started: float = field(default=0.0, init=False)

    async def __aenter__(self) -> Sampler:
        self._started = asyncio.get_running_loop().time()
        self._task = asyncio.create_task(self._sample(), name="loadtest-sampler")
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _sample(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                self.samples.append(await self.probe(loop.time() - self._started))
            except Exception:
                # A reading that could not be taken is a gap in the series,
                # not the end of the load test - and a gap is what it will
                # look like, since nothing is appended.
                logger.debug("a load-test reading failed", extra={"at": loop.time()})
            await asyncio.sleep(self.interval_seconds)

    def timeline(self, *, capacity: int) -> Timeline:
        return timeline_of(self.samples, interval=self.interval_seconds, capacity=capacity)
