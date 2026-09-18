"""The load profiles, as declared code rather than as command-line arguments.

The build plan names four offered loads - 10, 25, 50 and 100 concurrent
research jobs - so those four are here, in the repository, with the capacity
each is offered against. A profile someone can read is a profile someone can
argue with; a profile that only ever existed as a shell flag is a number in a
report that nobody can reproduce.

**Offered load and capacity are separate numbers on purpose.** A hundred jobs
submitted at once against four execution slots is not "a hundred concurrent
runs" - it is four running and ninety-six queued, which is exactly the shape a
deployment has and exactly the thing a queue-depth measurement is for. Calling
the offered figure the concurrency would make the interesting number - how deep
the backlog gets and how long a run waits in it - disappear into the label.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.enums import ResearchMode


@dataclass(frozen=True, slots=True)
class LoadProfile:
    """One load: how much is offered, against how much capacity, at what latency."""

    name: str
    #: Runs submitted. They arrive together unless ``arrival_seconds`` spreads
    #: them, because a burst is the load that finds the queueing behaviour.
    offered_jobs: int
    #: Execution slots per worker process (``worker_concurrency``).
    worker_concurrency: int = 4
    #: Worker processes sharing the queue.
    workers: int = 1
    #: Seconds the scripted provider takes to answer one call. Declared, not
    #: measured: it is an input to the test, and the report prints it as one.
    #: Zero measures the platform's own CPU cost with the provider removed.
    model_latency_seconds: float = 0.0
    #: Seconds over which the jobs are submitted. Zero is a burst.
    arrival_seconds: float = 0.0
    mode: ResearchMode = ResearchMode.DEEP
    #: How often the levels are read while the load runs.
    sample_interval_seconds: float = 0.25
    #: The whole profile fails rather than hangs after this.
    deadline_seconds: float = 900.0

    def __post_init__(self) -> None:
        if self.offered_jobs < 1:
            raise ValueError("A profile that offers no jobs measures nothing.")
        if self.worker_concurrency < 1 or self.workers < 1:
            raise ValueError("A profile with no capacity would never finish.")
        if self.model_latency_seconds < 0 or self.arrival_seconds < 0:
            raise ValueError("Time does not run backwards.")

    @property
    def capacity(self) -> int:
        """Runs that can execute at once across every worker in the profile."""
        return self.workers * self.worker_concurrency

    @property
    def queued_at_peak(self) -> int:
        """Runs that must wait if the whole burst arrives before any finishes."""
        return max(0, self.offered_jobs - self.capacity)


#: Seconds the scripted provider takes per call in the declared profiles. An
#: input, not a measurement, and low relative to a real structured call, which
#: is seconds. It is not zero because zero would be a different system: a run
#: that never awaits has nothing for another run to overlap with, so worker
#: concurrency would buy nothing and the queueing behaviour the profiles exist
#: to find would not occur. It is not three because the suite would then spend
#: an hour measuring `asyncio.sleep`. Half a second is enough for a run to be
#: interleaved and small enough that what the report shows is mostly this
#: system.
DECLARED_MODEL_LATENCY = 0.5

#: The four the build plan names. Capacity is held at four slots on one worker
#: across all of them, so the only thing that varies between the rows of the
#: report is the load - which is what makes the rows comparable. Four is the
#: default ``graph_max_concurrency``, and running more research than that per
#: process would oversubscribe a CPU that is already the bottleneck here.
DEFAULT_PROFILES: tuple[LoadProfile, ...] = (
    LoadProfile(name="offered-10", offered_jobs=10, model_latency_seconds=DECLARED_MODEL_LATENCY),
    LoadProfile(name="offered-25", offered_jobs=25, model_latency_seconds=DECLARED_MODEL_LATENCY),
    LoadProfile(name="offered-50", offered_jobs=50, model_latency_seconds=DECLARED_MODEL_LATENCY),
    LoadProfile(name="offered-100", offered_jobs=100, model_latency_seconds=DECLARED_MODEL_LATENCY),
)


def profile_named(name: str, profiles: tuple[LoadProfile, ...] = DEFAULT_PROFILES) -> LoadProfile:
    """The declared profile with this name, or a clear error naming the choices."""
    for profile in profiles:
        if profile.name == name:
            return profile
    raise KeyError(f"No such load profile: {name}. Declared: {[p.name for p in profiles]}")
