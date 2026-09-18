"""What one profile produced, and what the whole suite produced.

The shape of a result is the shape of the honesty rule. Every derived figure is
computed from the outcomes actually observed, every field that could be absent
is ``None``, and every ``None`` carries a reason in ``not_measured`` - so a
reader of the JSON can tell "we did not look" from "we looked and it was zero"
without reading the code that produced it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.core.enums import RunStatus
from app.loadtest.breakdown import Breakdown
from app.loadtest.measure import Latency, Timeline, summarise
from app.loadtest.profiles import LoadProfile
from app.models.limiter import Saturation

#: The statuses a run can end a load test in that mean it did what was asked.
SUCCEEDED = (RunStatus.COMPLETED,)


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """One run's timestamps, read back from its row after the load finished.

    From the row rather than from the load generator's own clock: the row is
    what the product reports to a user, and a generator that timed itself
    would be measuring its own bookkeeping alongside the system's.
    """

    run_id: uuid.UUID
    status: RunStatus
    created_at: dt.datetime
    started_at: dt.datetime | None
    completed_at: dt.datetime | None

    @property
    def queue_wait_seconds(self) -> float | None:
        """Submission to a worker claiming it."""
        if self.started_at is None:
            return None
        return (self.started_at - self.created_at).total_seconds()

    @property
    def execution_seconds(self) -> float | None:
        """A worker claiming it to the run being finished with."""
        if self.started_at is None or self.completed_at is None:
            return None
        return (self.completed_at - self.started_at).total_seconds()

    @property
    def total_seconds(self) -> float | None:
        """Submission to finished - what the person who asked experiences."""
        if self.completed_at is None:
            return None
        return (self.completed_at - self.created_at).total_seconds()


def _values(outcomes: Sequence[RunOutcome], attribute: str) -> list[float]:
    """The present readings of one timing, over the runs that succeeded.

    Only the successful ones: a run that failed in two seconds is not a fast
    run, and letting it into the distribution would make a degrading system
    look like an improving one.
    """
    return [
        value
        for outcome in outcomes
        if outcome.status in SUCCEEDED and (value := getattr(outcome, attribute)) is not None
    ]


@dataclass(frozen=True, slots=True)
class LoadResult:
    """One profile, measured."""

    profile: LoadProfile
    started_at: dt.datetime
    wall_seconds: float
    outcomes: tuple[RunOutcome, ...]
    timeline: Timeline
    #: The gateway's own throttle over this profile, or ``None`` when the
    #: profile did not run against a gateway that reports it.
    saturation: Saturation | None
    #: Where the node time went, from the ledger this profile wrote
    #: (Phase 22). ``None`` when the rows could not be read.
    breakdown: Breakdown | None = None
    #: Transactions Postgres committed while this profile ran, counted by
    #: Postgres (Phase 22). ``None`` when the counter could not be read.
    commits: int | None = None
    #: Why a field that could have been measured was not. Keyed by field.
    not_measured: dict[str, str] = field(default_factory=dict)

    @property
    def offered(self) -> int:
        return self.profile.offered_jobs

    @property
    def completed(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.status in SUCCEEDED)

    @property
    def completion_rate(self) -> float:
        """Completed over offered. Offered, not observed: a run that never got
        a row is a run the system lost, and that is the failure this catches."""
        return self.completed / self.offered if self.offered else 0.0

    @property
    def by_status(self) -> dict[str, int]:
        return dict(Counter(outcome.status.value for outcome in self.outcomes))

    @property
    def throughput_per_minute(self) -> float:
        """Completed runs per minute of wall clock, over the whole profile.

        Including the ramp and the tail, because that is the number a capacity
        plan needs: the steady-state figure of a system that never reaches
        steady state inside its own burst would flatter it.
        """
        return self.completed / self.wall_seconds * 60.0 if self.wall_seconds > 0 else 0.0

    @property
    def commits_per_run(self) -> float | None:
        """Database round trips one research run costs. The optimisation
        target Phase 22 chose, because it is the one number that is large,
        exactly measured, and paid on every run."""
        if self.commits is None or not self.completed:
            return None
        return self.commits / self.completed

    @property
    def total_latency(self) -> Latency | None:
        return summarise(_values(self.outcomes, "total_seconds"))

    @property
    def queue_latency(self) -> Latency | None:
        return summarise(_values(self.outcomes, "queue_wait_seconds"))

    @property
    def execution_latency(self) -> Latency | None:
        return summarise(_values(self.outcomes, "execution_seconds"))

    def as_dict(self) -> dict[str, object]:
        return {
            "profile": {
                "name": self.profile.name,
                "offered_jobs": self.profile.offered_jobs,
                "capacity": self.profile.capacity,
                "workers": self.profile.workers,
                "worker_concurrency": self.profile.worker_concurrency,
                "model_latency_seconds": self.profile.model_latency_seconds,
                "arrival_seconds": self.profile.arrival_seconds,
                "mode": self.profile.mode.value,
            },
            "started_at": self.started_at.isoformat(),
            "wall_seconds": round(self.wall_seconds, 3),
            "offered": self.offered,
            "completed": self.completed,
            "completion_rate": round(self.completion_rate, 4),
            "by_status": self.by_status,
            "throughput_runs_per_minute": round(self.throughput_per_minute, 3),
            "latency_seconds": {
                "total": None if self.total_latency is None else self.total_latency.as_dict(),
                "queue_wait": None if self.queue_latency is None else self.queue_latency.as_dict(),
                "execution": (
                    None if self.execution_latency is None else self.execution_latency.as_dict()
                ),
            },
            "timeline": self.timeline.as_dict(),
            "llm_throttling": (
                None
                if self.saturation is None
                else {
                    "limit": self.saturation.limit,
                    "acquisitions": self.saturation.acquisitions,
                    "waited": self.saturation.waited,
                    "waited_fraction": (
                        round(self.saturation.waited / self.saturation.acquisitions, 4)
                        if self.saturation.acquisitions
                        else None
                    ),
                    "mean_wait_seconds": round(self.saturation.mean_wait_seconds, 4),
                    "max_wait_seconds": round(self.saturation.max_wait_seconds, 4),
                    "max_in_flight": self.saturation.max_in_flight,
                    "utilisation": round(self.saturation.utilisation, 3),
                }
            ),
            "database": {
                "commits": self.commits,
                "commits_per_run": (
                    None if self.commits_per_run is None else round(self.commits_per_run, 1)
                ),
            },
            "breakdown": None if self.breakdown is None else self.breakdown.as_dict(),
            "not_measured": dict(self.not_measured),
        }


@dataclass(frozen=True, slots=True)
class LoadSuite:
    """Every profile that ran, and the conditions they all ran under.

    ``conditions`` is not decoration. A throughput figure is meaningless
    without the machine, the queue implementation and what was answering the
    model calls, and the one place those can be attached so that they cannot
    be separated from the numbers is the artefact itself.
    """

    results: tuple[LoadResult, ...]
    conditions: dict[str, str]
    #: Profiles that were declared and did not run, and why.
    skipped: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "conditions": dict(self.conditions),
            "skipped": dict(self.skipped),
            "results": [result.as_dict() for result in self.results],
        }
