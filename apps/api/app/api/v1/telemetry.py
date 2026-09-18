"""What this process is doing, for a scraper and for a person (Phase 17).

Two endpoints that answer the same question at different resolutions.

``GET /metrics`` is the Prometheus exposition format, unversioned and outside
`/api/v1` because a scraper is not an API client - the path is in
`infra/monitoring/prometheus.yml` and changing it would silently stop the
scrape rather than fail a build.

``GET /api/v1/evaluations/system`` is the live panel the product renders. It is
computed from rows rather than from the metrics above, because a process
cannot query its own exposition endpoint and a second collector would be a
second set of numbers to disagree with the first.

``GET /api/v1/evaluations`` is the benchmark surface (Phase 18). It returns
``latest: null`` when nothing has been executed, which the page renders as "no
benchmark has run" - never as an empty chart of zeros.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import DatabaseDep, MetricsDep, get_evaluation_store, get_system_metrics
from app.db.repositories.evaluations import SqlAlchemyEvaluationStore
from app.db.repositories.metrics import SqlAlchemySystemMetrics
from app.evaluations.schemas import EvaluationsResponse
from app.observability.metrics import CONTENT_TYPE, observe_pool
from app.observability.system import SystemMetrics

metrics_router = APIRouter(tags=["telemetry"])
system_router = APIRouter(tags=["telemetry"])


@metrics_router.get(
    "/metrics",
    summary="Prometheus metrics",
    include_in_schema=False,
    response_class=Response,
)
async def metrics(database: DatabaseDep, request_metrics: MetricsDep) -> Response:
    """Render this process's counters.

    The pool gauges are read here rather than tracked continuously: SQLAlchemy
    already counts them, and a second count kept in step by hand would be the
    one that is wrong.

    404 when metrics are switched off, which is the honest answer - an empty
    exposition would read as a healthy process reporting nothing.
    """
    if request_metrics is None:
        return Response(status_code=404)
    observe_pool(request_metrics, database.engine)
    return Response(content=request_metrics.render(), media_type=CONTENT_TYPE)


@system_router.get(
    "/evaluations/system",
    response_model=SystemMetrics,
    summary="Live system metrics",
)
async def system_metrics(
    reader: Annotated[SqlAlchemySystemMetrics, Depends(get_system_metrics)],
    window: Annotated[str, Query(pattern="^(1h|24h|7d)$")] = "24h",
) -> SystemMetrics:
    """Success rate, latency, spend and saturation over a window.

    Every field is nullable and null means *not measured*: a window in which
    no run finished has no median runtime, and rendering zero there would say
    something false and specific.
    """
    return await reader.snapshot(window)


@system_router.get(
    "/evaluations",
    response_model=EvaluationsResponse,
    summary="Benchmark results",
)
async def evaluations(
    store: Annotated[SqlAlchemyEvaluationStore, Depends(get_evaluation_store)],
) -> EvaluationsResponse:
    """The latest benchmark, its cases, and the history behind it.

    Unauthenticated like the other telemetry here: a benchmark result belongs
    to the build rather than to a person, and it carries no user content -
    only case ids, questions from the committed dataset, and measured numbers.
    """
    return await store.latest()
