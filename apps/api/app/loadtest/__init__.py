"""Load testing: what this system does when more is asked of it than it can do (Phase 21).

The build plan asks for four numbers at four offered loads - throughput, queue
depth, completion rate, and the latency percentiles - plus the utilisation of
everything that could be the ceiling. This package is the measuring apparatus:
the profiles as declared code, the percentile arithmetic, the sampler that
watches the levels while the load runs, and the report that comes out. It does
not know how a run is executed, which is what lets the same apparatus measure
the worker pipeline and the HTTP surface.

Three rules, all of them the same rule.

**A number that was not measured is not reported.** Every field that could be
absent is nullable and ``None`` means *not measured*. A load test run without
Redis reports Redis utilisation as null, never as zero, and the report says
why.

**The conditions travel with the numbers.** A profile records the simulated
model latency, the worker capacity and the queue implementation, and the report
prints them beside the results. "P95 was 9.4 s" is not a fact about anything
until it also says what was answering the model calls.

**Nothing here writes a number it did not see.** There is no expected value, no
extrapolation and no baseline to fall back on. A profile that could not run
appears in the report as *not run*, with the reason.
"""

from __future__ import annotations

from app.loadtest.breakdown import Breakdown, NodeCost, breakdown_of
from app.loadtest.measure import Latency, Sample, Sampler, Timeline, percentile, summarise
from app.loadtest.profiles import DEFAULT_PROFILES, LoadProfile, profile_named
from app.loadtest.report import render_markdown
from app.loadtest.results import LoadResult, LoadSuite, RunOutcome

__all__ = [
    "DEFAULT_PROFILES",
    "Breakdown",
    "Latency",
    "LoadProfile",
    "LoadResult",
    "LoadSuite",
    "NodeCost",
    "RunOutcome",
    "Sample",
    "Sampler",
    "Timeline",
    "breakdown_of",
    "percentile",
    "profile_named",
    "render_markdown",
    "summarise",
]
