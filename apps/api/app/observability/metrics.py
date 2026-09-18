"""Prometheus metrics: the numbers an on-call engineer reads first (Phase 17).

Everything here is already measured somewhere - the gateway prices its calls,
the tool executor times its own, the worker knows how a run ended. What was
missing is an aggregate: a log line answers "what happened to this run" and a
metric answers "what is happening to all of them", and no amount of grepping
turns the first into the second.

**One registry, owned by this module, not the library's global default.**
``prometheus_client`` registers into a process-wide default, which raises on a
duplicate name - so a second application instance in one test session, or a
module imported twice, brings the process down over telemetry. A registry that
belongs to the app is also the only way a test can read a counter and be sure
it is reading its own.

**Labels are bounded, and the bound is deliberate.** A label whose values are
unbounded - a URL, a run id, a query - turns one metric into a million time
series and takes Prometheus down with it. So a path is the *route template*
rather than the URL, and a run id never appears. What belongs per run is in
the ledger (Phase 16), which is a database and is built for it.

**Metrics never fail the work.** Every observation is wrapped by the callers
that emit them, for the same reason the ledger writes are: a research run that
failed because a counter did is a worse outcome than a missing number.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - import cost, see the note below
    from prometheus_client import CollectorRegistry

    from app.models.limiter import Saturation

logger = get_logger(__name__)

#: Seconds. A research run is minutes, an HTTP request is milliseconds, and a
#: model call is somewhere between - so three ladders rather than one, because
#: a histogram whose buckets do not straddle the real distribution reports
#: percentiles that are technically correct and practically useless.
REQUEST_BUCKETS = (0.005, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
CALL_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0)
RUN_BUCKETS = (5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0)

#: Waiting for the gateway's own concurrency slot (Phase 21). A fourth
#: ladder because the interesting region is *below* a call's: zero is the
#: common case and a tenth of a second already means the ceiling is biting,
#: which `CALL_BUCKETS` would put in its first bucket and hide.
SLOT_BUCKETS = (0.001, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@dataclass(slots=True)
class Metrics:
    """The instruments, created once per process.

    Held on a dataclass rather than as module globals so that a test can build
    a second, independent set - and so that "which metrics exist" is a list
    someone can read rather than a grep.
    """

    registry: CollectorRegistry
    http_requests: Any
    http_duration: Any
    research_runs: Any
    research_duration: Any
    llm_calls: Any
    llm_duration: Any
    llm_tokens: Any
    llm_cost: Any
    llm_slot_wait: Any
    llm_in_flight: Any
    tool_calls: Any
    tool_duration: Any
    cache_lookups: Any
    retrieval_duration: Any
    retrieval_results: Any
    queue_depth: Any
    active_workers: Any
    db_pool: Any

    def render(self) -> bytes:
        """The exposition format Prometheus scrapes."""
        from prometheus_client import generate_latest

        return bytes(generate_latest(self.registry))


def build_metrics(namespace: str = "aether") -> Metrics:
    """Create one process's instruments on a registry of their own.

    ``prometheus_client`` is imported here rather than at module scope for the
    reason the repository applies to every third-party import that a test
    process would otherwise pay for: this module is imported by the settings
    wiring, and a process that exports no metrics should not pay to define
    them.
    """
    from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

    registry = CollectorRegistry()

    def counter(name: str, doc: str, labels: tuple[str, ...] = ()) -> Any:
        return Counter(name, doc, labels, namespace=namespace, registry=registry)

    def gauge(name: str, doc: str, labels: tuple[str, ...] = ()) -> Any:
        return Gauge(name, doc, labels, namespace=namespace, registry=registry)

    def histogram(name: str, doc: str, buckets: tuple[float, ...], labels: tuple[str, ...]) -> Any:
        return Histogram(name, doc, labels, namespace=namespace, registry=registry, buckets=buckets)

    return Metrics(
        registry=registry,
        http_requests=counter(
            "http_requests_total",
            "HTTP requests, by route template and outcome.",
            ("method", "route", "status"),
        ),
        http_duration=histogram(
            "http_request_duration_seconds",
            "Time to serve an HTTP request.",
            REQUEST_BUCKETS,
            ("method", "route"),
        ),
        research_runs=counter(
            "research_runs_total",
            "Research runs a worker finished with, by outcome.",
            ("status",),
        ),
        research_duration=histogram(
            "research_run_duration_seconds",
            "Wall-clock time from a worker claiming a run to finishing it.",
            RUN_BUCKETS,
            ("status",),
        ),
        llm_calls=counter(
            "llm_calls_total",
            "Model call attempts, including the ones that failed and the ones served from cache.",
            ("provider", "model", "role", "status", "cache_hit"),
        ),
        llm_duration=histogram(
            "llm_call_duration_seconds",
            "Time a model call took, cache hits included (they take almost none).",
            CALL_BUCKETS,
            ("provider", "model"),
        ),
        llm_tokens=counter(
            "llm_tokens_total",
            "Tokens billed, split by direction.",
            ("provider", "model", "kind"),
        ),
        llm_cost=counter(
            "llm_cost_usd_total",
            "Estimated spend. Calls the registry cannot price add nothing here "
            "and are counted as uncosted on the run instead.",
            ("provider", "model"),
        ),
        llm_slot_wait=histogram(
            "llm_slot_wait_seconds",
            "Time a model call spent queued behind this process's own "
            "concurrency ceiling, before the provider was contacted.",
            SLOT_BUCKETS,
            (),
        ),
        llm_in_flight=gauge(
            "llm_calls_in_flight",
            "Model calls holding a gateway slot right now.",
        ),
        tool_calls=counter(
            "tool_calls_total",
            "Tool call attempts, by tool and outcome.",
            ("tool", "status", "cache_hit"),
        ),
        tool_duration=histogram(
            "tool_call_duration_seconds",
            "Time a tool call took.",
            CALL_BUCKETS,
            ("tool",),
        ),
        cache_lookups=counter(
            "cache_lookups_total",
            "Cache lookups by namespace and where the value came from.",
            ("namespace", "origin"),
        ),
        retrieval_duration=histogram(
            "retrieval_duration_seconds",
            "Time one retrieval call took, end to end.",
            CALL_BUCKETS,
            (),
        ),
        retrieval_results=histogram(
            "retrieval_results",
            "Chunks one retrieval call returned after fusion and reranking.",
            (0.0, 1.0, 2.0, 4.0, 8.0, 12.0, 20.0, 50.0),
            (),
        ),
        queue_depth=gauge("queue_depth", "Research jobs waiting to be claimed."),
        active_workers=gauge(
            "active_workers", "Workers holding a run whose lease has not expired."
        ),
        db_pool=gauge(
            "db_pool_connections",
            "Database pool occupancy, so saturation is visible before it is felt.",
            ("state",),
        ),
    )


def serve_metrics(metrics: Metrics, *, port: int) -> Any:
    """Expose ``metrics`` over HTTP, for a process that serves nothing else.

    The worker has no web server, and Prometheus pulls - so it needs a socket
    of its own. ``prometheus_client`` starts a daemon thread, which is exactly
    the right weight for a scrape endpoint: it must answer while the event loop
    is busy inside a node, which is precisely when someone wants to look.
    """
    from prometheus_client import start_http_server

    # The daemon thread is the library's; the server is what a caller shuts
    # down, and it is the only half worth returning.
    server, _thread = start_http_server(port, registry=metrics.registry)
    logger.info("worker metrics exposed", extra={"port": port})
    return server


def observe_pool(metrics: Metrics, engine: Any) -> None:
    """Record what the database pool is holding right now.

    Read at scrape time rather than tracked continuously: SQLAlchemy already
    counts this, and a second count kept in step by hand would be the one that
    is wrong.
    """
    try:
        pool = engine.pool
        metrics.db_pool.labels(state="in_use").set(pool.checkedout())
        metrics.db_pool.labels(state="idle").set(pool.checkedin())
        metrics.db_pool.labels(state="overflow").set(max(0, pool.overflow()))
    except Exception as exc:  # pragma: no cover - pool internals vary by dialect
        logger.debug("could not read the database pool", extra={"error": str(exc)})


def bind_levels(metrics: Metrics, *, engine: Any, saturation: Callable[[], Saturation]) -> None:
    """Have the level gauges read themselves whenever Prometheus collects.

    The API calls ``observe_pool`` from inside its own ``/metrics`` handler,
    so its pool gauge is fresh on every scrape. The worker has no handler -
    ``prometheus_client`` serves the registry from a thread of its own - so a
    gauge nothing writes reads as zero forever, which is the difference
    between *not measured* and *zero* that this repository refuses to blur.
    A collect-time callback is how a process without a request exports a level,
    and it runs on the scrape thread, which is why every reader below is a
    plain attribute access and nothing here awaits.
    """
    pool = engine.pool
    metrics.db_pool.labels(state="in_use").set_function(lambda: _level(pool.checkedout))
    metrics.db_pool.labels(state="idle").set_function(lambda: _level(pool.checkedin))
    metrics.db_pool.labels(state="overflow").set_function(lambda: max(0.0, _level(pool.overflow)))
    metrics.llm_in_flight.set_function(lambda: float(saturation().in_flight))


def _level(read: Callable[[], int]) -> float:
    """A pool counter, or zero if this dialect's pool does not keep it."""
    try:
        return float(read())
    except Exception as exc:  # pragma: no cover - pool internals vary by dialect
        logger.debug("could not read a pool level", extra={"error": str(exc)})
        return 0.0
