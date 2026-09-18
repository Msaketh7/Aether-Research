"""Metrics, traces and the live panel (Phase 17).

Three claims are worth testing and the rest is plumbing: the labels are
bounded, a measurement that was never taken is reported as absent rather than
as zero, and none of this can break the work it watches.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from httpx import AsyncClient

from app.cache import CacheNamespace, InMemoryCache, ResponseCache
from app.cache.store import CachePolicy
from app.core.config import Settings
from app.core.enums import RunStatus
from app.db.repositories.metrics import SqlAlchemySystemMetrics
from app.db.repositories.trace import SqlAlchemyTraceStore
from app.models.recording import LlmCallRecord
from app.observability.instruments import (
    MeteredCallRecorder,
    MeteredToolRecorder,
    observe_cache,
    observe_run,
)
from app.observability.metrics import build_metrics
from app.observability.middleware import UNMATCHED_ROUTE
from app.observability.tracing import build_langsmith_client, current_ids
from app.sources.base import CollectingToolRecorder
from app.workers.queue import InMemoryJobQueue
from tests.conftest import API, valid_request
from tests.support.worker import seed_run, set_row, worker_settings
from tests.test_ledger import llm_record, tool_record


@pytest.fixture
def worker_config(settings):
    return worker_settings(settings)


@pytest.fixture
def metrics():
    """A registry of this test's own, never the library's process-wide one."""
    return build_metrics()


def sample(metrics, name: str, **labels: str) -> float | None:
    """One sample's value, or ``None`` when it has never been observed."""
    return metrics.registry.get_sample_value(name, labels or None)


# --- the exposition endpoint ----------------------------------------------


async def test_the_metrics_endpoint_renders_the_exposition_format(client: AsyncClient):
    response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "aether_http_requests_total" in body
    assert "aether_db_pool_connections" in body


async def test_metrics_can_be_turned_off(settings: Settings):
    """A 404 rather than an empty exposition, which would read as a healthy
    process reporting nothing."""
    from httpx import ASGITransport
    from httpx import AsyncClient as Client

    from app.main import create_app

    app = create_app(settings.model_copy(update={"metrics_enabled": False}))
    async with (
        Client(transport=ASGITransport(app=app), base_url="http://testserver") as http,
        app.router.lifespan_context(app),
    ):
        assert (await http.get("/metrics")).status_code == 404


async def test_a_request_is_counted_by_route_template_not_by_url(client: AsyncClient):
    """The failure this prevents: one time series per run id."""
    created = await client.post(f"{API}/research", json=valid_request())
    run_id = created.json()["run_id"]
    await client.get(f"{API}/research/{run_id}")

    body = (await client.get("/metrics")).text

    assert 'route="/api/v1/research/{run_id}"' in body
    assert run_id not in body


async def test_a_path_that_matched_no_route_is_not_a_label(client: AsyncClient):
    """An unmatched path is attacker-controlled, and an attacker-controlled
    label value is unbounded cardinality with somebody behind it."""
    await client.get("/api/v1/does-not-exist-9f3c")

    body = (await client.get("/metrics")).text

    assert f'route="{UNMATCHED_ROUTE}"' in body
    assert "does-not-exist-9f3c" not in body


# --- what each instrument records -----------------------------------------


async def test_a_model_call_is_counted_timed_and_costed(metrics):
    recorder = MeteredCallRecorder(metrics, _Silent())

    await recorder.record(llm_record(uuid.uuid4(), cost=0.25))

    labels = {
        "provider": "anthropic",
        "model": "claude-x",
        "role": "planner",
        "status": "ok",
        "cache_hit": "false",
    }
    assert sample(metrics, "aether_llm_calls_total", **labels) == 1
    assert (
        sample(
            metrics,
            "aether_llm_tokens_total",
            provider="anthropic",
            model="claude-x",
            kind="prompt",
        )
        == 100
    )
    assert (
        sample(metrics, "aether_llm_cost_usd_total", provider="anthropic", model="claude-x") == 0.25
    )


async def test_an_unpriced_call_adds_nothing_to_the_spend_counter(metrics):
    """Not zero - nothing. A model with no declared price is visible as an
    uncosted call on its run, not as an hour that happened to be free."""
    recorder = MeteredCallRecorder(metrics, _Silent())

    await recorder.record(llm_record(uuid.uuid4(), cost=None))

    assert (
        sample(
            metrics,
            "aether_llm_calls_total",
            provider="anthropic",
            model="claude-x",
            role="planner",
            status="ok",
            cache_hit="false",
        )
        == 1
    )
    assert (
        sample(metrics, "aether_llm_cost_usd_total", provider="anthropic", model="claude-x") is None
    )


async def test_a_tool_call_records_whether_it_cost_a_request(metrics):
    recorder = MeteredToolRecorder(metrics, CollectingToolRecorder())

    await recorder.record(tool_record(cache_hit=False))
    await recorder.record(tool_record(cache_hit=True))

    assert (
        sample(metrics, "aether_tool_calls_total", tool="search", status="ok", cache_hit="false")
        == 1
    )
    assert (
        sample(metrics, "aether_tool_calls_total", tool="search", status="ok", cache_hit="true")
        == 1
    )


async def test_the_cache_reports_where_each_value_came_from(metrics):
    """Three origins, not two: a stored value and one that joined a call in
    flight both cost nothing and are different optimisations."""
    cache = ResponseCache(
        InMemoryCache(),
        CachePolicy(
            ttl_seconds=dict.fromkeys(CacheNamespace, 60),
            enabled=frozenset(CacheNamespace),
            max_value_bytes=4096,
        ),
        observer=lambda namespace, origin: observe_cache(metrics, namespace, origin),
    )

    async def loader() -> str:
        return "value"

    await cache.through(CacheNamespace.SEARCH, "q", loader=loader, encode=str, decode=str)
    await cache.through(CacheNamespace.SEARCH, "q", loader=loader, encode=str, decode=str)

    assert sample(metrics, "aether_cache_lookups_total", namespace="search", origin="loader") == 1
    assert sample(metrics, "aether_cache_lookups_total", namespace="search", origin="store") == 1


async def test_a_run_with_no_known_start_is_counted_but_not_timed(metrics):
    """A histogram with a false observation in it is worse than one with a gap."""
    observe_run(metrics, status=RunStatus.COMPLETED, seconds=None)

    assert sample(metrics, "aether_research_runs_total", status="completed") == 1
    assert sample(metrics, "aether_research_run_duration_seconds_count", status="completed") is None


async def test_an_instrument_that_raises_does_not_reach_the_caller(metrics):
    """A research run that failed because a counter did would be an outage
    caused by watching for one."""
    metrics.llm_calls = _Exploding()
    recorder = MeteredCallRecorder(metrics, _Silent())

    await recorder.record(llm_record(uuid.uuid4()))


# --- retrieval -------------------------------------------------------------


async def test_a_retrieval_is_timed_and_its_results_counted(metrics):
    from app.observability.retrieval import MeasuredRetriever

    retriever = MeasuredRetriever(_StubRetriever(chunks=7), metrics)

    await retriever.retrieve("pricing", run_id=uuid.uuid4(), user_id=uuid.uuid4())

    assert sample(metrics, "aether_retrieval_duration_seconds_count") == 1
    assert sample(metrics, "aether_retrieval_results_sum") == 7


async def test_a_retrieval_that_failed_is_still_timed(metrics):
    """The slow failures are the ones worth seeing."""
    from app.observability.retrieval import MeasuredRetriever

    retriever = MeasuredRetriever(_StubRetriever(raises=RuntimeError("no index")), metrics)

    with pytest.raises(RuntimeError):
        await retriever.retrieve("pricing", run_id=uuid.uuid4(), user_id=uuid.uuid4())

    assert sample(metrics, "aether_retrieval_duration_seconds_count") == 1


# --- tracing ---------------------------------------------------------------


def test_nothing_is_traced_until_something_is_configured(settings: Settings):
    """The default: every span in the code still runs, against the API's
    no-op implementation, and records nothing."""
    assert settings.otel_exporter_otlp_endpoint is None
    ids = current_ids()

    assert (ids.trace_id, ids.span_id) == (None, None)
    assert ids.recording is False


def test_langsmith_stays_off_unless_a_deployment_asks(settings: Settings):
    """Prompts and retrieved documents leaving for a third party is a
    decision, not something a shell variable can make (Phase 9's finding)."""
    assert build_langsmith_client(settings) is None


def test_langsmith_switched_on_without_a_key_stays_off(settings: Settings):
    """Half-configured is off, not half-on."""
    asked = settings.model_copy(update={"langsmith_tracing": True, "langsmith_api_key": None})

    assert build_langsmith_client(asked) is None


# --- the live panel --------------------------------------------------------


@pytest.fixture
def panel(database, worker_config) -> SqlAlchemySystemMetrics:
    return SqlAlchemySystemMetrics(
        database, InMemoryJobQueue(), lease_seconds=worker_config.worker_lease_seconds
    )


async def test_an_empty_window_reports_nothing_measured(panel):
    """Not zero. "No run finished" and "every run failed" are different
    facts, and the panel must not turn the first into the second."""
    snapshot = await panel.snapshot("1h")

    assert snapshot.research_success_rate is None
    assert snapshot.research_failure_rate is None
    assert snapshot.latency_p50_seconds is None
    assert snapshot.cache_hit_rate is None
    assert snapshot.total_tokens is None
    assert snapshot.total_cost_usd is None
    assert snapshot.queue_depth == 0


async def test_finished_runs_give_a_success_rate_and_percentiles(database, worker_config, panel):
    now = dt.datetime.now(dt.UTC)
    for index, status in enumerate((RunStatus.COMPLETED, RunStatus.COMPLETED, RunStatus.FAILED)):
        run = await seed_run(database, worker_config)
        await set_row(
            database,
            run.id,
            status=status.value,
            started_at=now - dt.timedelta(seconds=30 + index),
            completed_at=now,
        )

    snapshot = await panel.snapshot("24h")

    assert snapshot.research_success_rate == pytest.approx(2 / 3, abs=1e-4)
    assert snapshot.research_failure_rate == pytest.approx(1 / 3, abs=1e-4)
    assert snapshot.latency_p50_seconds is not None and snapshot.latency_p50_seconds >= 30


async def test_one_unpriced_call_makes_the_total_unmeasured(database, worker_config, panel):
    """A partial sum presented as a total is the specific dishonesty the
    whole system is built to avoid."""
    run = await seed_run(database, worker_config)
    store = SqlAlchemyTraceStore(database)
    await store.record_llm_call(llm_record(run.id, cost=0.25), agent_run_id=None)
    await store.record_llm_call(llm_record(run.id, cost=None), agent_run_id=None)

    snapshot = await panel.snapshot("24h")

    assert snapshot.total_tokens == 300, "tokens are still counted"
    assert snapshot.total_cost_usd is None


async def test_the_cache_hit_rate_spans_models_and_tools(database, worker_config, panel):
    run = await seed_run(database, worker_config)
    store = SqlAlchemyTraceStore(database)
    await store.record_llm_call(_cached(llm_record(run.id)), agent_run_id=None)
    await store.record_llm_call(llm_record(run.id), agent_run_id=None)

    snapshot = await panel.snapshot("24h")

    assert snapshot.cache_hit_rate == pytest.approx(0.5)
    assert snapshot.llm_latency_p95_ms is not None


async def test_the_endpoint_serves_the_panel(client: AsyncClient):
    response = await client.get(f"{API}/evaluations/system?window=1h")

    assert response.status_code == 200
    body = response.json()
    assert body["window"] == "1h"
    # Nothing has run in this window, and the contract says so with nulls.
    assert body["research_success_rate"] is None
    assert body["active_workers"] == 0


async def test_an_unknown_window_is_refused(client: AsyncClient):
    assert (await client.get(f"{API}/evaluations/system?window=forever")).status_code == 422


def _cached(call: LlmCallRecord) -> LlmCallRecord:
    import dataclasses

    return dataclasses.replace(call, cache_hit=True)


class _Silent:
    async def record(self, call: object) -> None:
        return None


class _Exploding:
    def labels(self, **_: str) -> object:
        raise RuntimeError("the registry is unhappy")


class _StubRetriever:
    """A retriever that returns a fixed number of chunks, or fails."""

    def __init__(self, *, chunks: int = 0, raises: Exception | None = None) -> None:
        self._chunks = chunks
        self._raises = raises

    async def retrieve(self, query: str, **_: object) -> object:
        if self._raises is not None:
            raise self._raises
        return _Result(self._chunks)

    async def retrieve_with_filters(self, query: str, **_: object) -> object:
        return await self.retrieve(query)

    async def retrieve_hybrid(self, query: str, **_: object) -> object:
        return await self.retrieve(query)


class _Result:
    def __init__(self, count: int) -> None:
        self.chunks = [object()] * count


# --- the ids that join a row to a trace -----------------------------------


async def test_a_ledger_row_carries_the_trace_it_happened_in(database, store_and_span):
    """The columns have been there since Phase 3; this is what fills them.

    Exercised against a real tracer provider rather than by asserting that
    ``None`` is stored, because the interesting case is the one a deployment
    with a collector gets.
    """
    store, run_id, recorded = store_and_span

    activity = await store.activity_for(run_id)
    (agent,) = activity.agent_runs
    (call,) = activity.llm_calls
    assert agent.trace_id is not None and len(agent.trace_id) == 32
    assert agent.span_id is not None and len(agent.span_id) == 16
    # The model call is inside the node's span, so they share a trace.
    assert call.agent_run_id == agent.id
    assert recorded, "the exporter saw the spans that produced those ids"


@pytest.fixture
async def store_and_span(database, worker_config):
    """A node span and a model call inside it, with a real tracer recording.

    The provider is installed on this test's own ``TracerProvider`` and the
    spans go to an in-memory exporter, so nothing leaves the process and the
    global provider the rest of the suite sees is left alone.
    """
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app.agents.schemas import GraphNode
    from app.observability.ledger import DatabaseCallRecorder, DatabaseTracer

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    run = await seed_run(database, worker_config)
    store = SqlAlchemyTraceStore(database)

    with provider.get_tracer("test").start_as_current_span("run"):
        tracer = DatabaseTracer(store)
        async with tracer.span(GraphNode.PLANNER, research_id=run.id, iteration=1) as node:
            await DatabaseCallRecorder(store, _Silent()).record(llm_record(run.id))
            node.succeeded(_no_usage())

    yield store, run.id, exporter.get_finished_spans()


def _no_usage() -> object:
    from app.agents.schemas import NodeUsage

    return NodeUsage()
