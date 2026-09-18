"""The live system panel on `/evaluations` (Phase 17).

The same question Prometheus answers, asked by the product instead of by an
operator - and answered from the database rather than from the scrape, because
the application cannot query its own metrics endpoint and a second collector
would be a second set of numbers to disagree with the first.

**Every field is nullable, and null means "not measured".** A window in which
no run finished has no median runtime; a window in which no model was called
has no cache-hit rate. Rendering either as ``0`` would say something false and
specific - "runs are instant", "the cache never hits" - which is worse than
saying nothing. The frontend already distinguishes the two (`MetricValue` in
``@aether/shared-types``), and this is the half of that contract the backend
owes it.

**Percentiles are computed by Postgres.** ``percentile_cont`` over the window,
rather than pulling the rows out and sorting them here: the window is a day of
runs, and moving them across the wire to compute one number would be the
expensive kind of convenience.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import Field

from app.research.schemas import ApiModel

#: The windows the panel offers. A closed set, because each one is an index
#: the queries below rely on rather than an arbitrary range.
type MetricsWindow = Literal["1h", "24h", "7d"]

WINDOW_SPANS: dict[str, dt.timedelta] = {
    "1h": dt.timedelta(hours=1),
    "24h": dt.timedelta(hours=24),
    "7d": dt.timedelta(days=7),
}


class SystemMetrics(ApiModel):
    """What the system has been doing lately. Mirrors `SystemMetrics` in
    ``@aether/shared-types``; every number here was measured."""

    window: str = Field(description="The period these numbers cover.")

    #: Of the runs that reached a terminal status in the window. ``None`` when
    #: none did - not 0.0, which would read as "everything failed".
    research_success_rate: float | None = None
    research_failure_rate: float | None = None

    latency_p50_seconds: float | None = None
    latency_p95_seconds: float | None = None
    latency_p99_seconds: float | None = None

    llm_latency_p95_ms: float | None = None
    tool_latency_p95_ms: float | None = None

    #: Across model and tool calls together: both are "work we did not repeat".
    cache_hit_rate: float | None = None

    total_tokens: int | None = None
    #: ``None`` when any call in the window could not be priced, for the
    #: reason the whole system applies: a total that silently omits what it
    #: could not measure reads as authoritative and is not.
    total_cost_usd: float | None = None

    queue_depth: int | None = None
    active_workers: int | None = None
