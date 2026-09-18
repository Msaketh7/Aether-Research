"""The execution trace, written and read back (Phase 16, FR-10).

Against real Postgres, because the interesting parts are the three
constraints the tables carry: a tool call cannot exist without an agent run,
a cost column can be null and must be when the price is unknown, and the
vocabulary columns are closed lists a check constraint enforces. A fake store
would satisfy the protocol and prove none of it.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.agents.schemas import CostEstimate, GraphNode, NodeError, NodeUsage, TokenCount
from app.core.enums import (
    AgentName,
    AgentStatus,
    LlmCallStatus,
    LlmProvider,
    ResearchMode,
    RunStatus,
    ToolName,
    ToolStatus,
)
from app.db.repositories.trace import SqlAlchemyTraceStore
from app.models.recording import LlmCallRecord
from app.observability.ledger import (
    DatabaseCallRecorder,
    DatabaseToolRecorder,
    DatabaseTracer,
    current_agent_run,
)
from app.sources.base import CollectingToolRecorder, ToolCallRecord
from tests.conftest import API
from tests.support.worker import build_worker, seed_run, serve_until, settled, worker_settings


@pytest.fixture
def worker_config(settings):
    return worker_settings(settings)


@pytest.fixture
def store(database) -> SqlAlchemyTraceStore:
    return SqlAlchemyTraceStore(database)


def usage(tokens: int = 150, cost: float | None = 0.02) -> NodeUsage:
    return NodeUsage(
        tokens=TokenCount(prompt_tokens=tokens, completion_tokens=0),
        cost=CostEstimate(usd=cost or 0.0, uncosted_calls=0 if cost is not None else 1),
    )


def llm_record(run_id: uuid.UUID | None, *, cost: float | None = 0.02) -> LlmCallRecord:
    return LlmCallRecord(
        role=AgentName.PLANNER,
        mode=ResearchMode.DEEP,
        provider=LlmProvider.ANTHROPIC,
        model="claude-x",
        operation="generate",
        status=LlmCallStatus.OK,
        prompt_tokens=100,
        completion_tokens=50,
        cost_usd=cost,
        latency_ms=42,
        prompt_version="planner-v3",
        attempt=1,
        run_id=run_id,
    )


def tool_record(*, cache_hit: bool = False) -> ToolCallRecord:
    return ToolCallRecord(
        tool_name=ToolName.SEARCH,
        status=ToolStatus.OK,
        request={"query": "inference pricing"},
        response_summary={"results": 7},
        latency_ms=120,
        attempt=1,
        cache_hit=cache_hit,
    )


# --- spans ----------------------------------------------------------------


async def test_a_node_execution_becomes_a_row(database, store, worker_config):
    run = await seed_run(database, worker_config)
    tracer = DatabaseTracer(store)

    async with tracer.span(GraphNode.PLANNER, research_id=run.id, iteration=1) as span:
        assert span.id is not None
        span.succeeded(usage(), summary="4 subtasks")

    activity = await store.activity_for(run.id)
    (row,) = activity.agent_runs
    assert (row.agent_name, row.iteration, row.status) == (AgentName.PLANNER, 1, AgentStatus.OK)
    assert row.summary == "4 subtasks"
    assert row.tokens == 150
    assert row.completed_at is not None and row.latency_ms is not None


async def test_a_researcher_row_names_the_subtask_it_was_given(database, store, worker_config):
    run = await seed_run(database, worker_config)
    tracer = DatabaseTracer(store)

    async with tracer.span(
        GraphNode.RESEARCHER, research_id=run.id, iteration=2, task_key="pricing"
    ) as span:
        span.succeeded(usage())

    (row,) = (await store.activity_for(run.id)).agent_runs
    assert (row.task_external_id, row.iteration) == ("pricing", 2)


async def test_a_failed_node_records_why(database, store, worker_config):
    run = await seed_run(database, worker_config)
    tracer = DatabaseTracer(store)

    async with tracer.span(GraphNode.CRITIC, research_id=run.id, iteration=1) as span:
        span.failed(
            NodeError(
                node=GraphNode.CRITIC,
                iteration=1,
                code="node_timeout",
                message="The step did not finish within its time limit.",
            )
        )

    (row,) = (await store.activity_for(run.id)).agent_runs
    assert row.status is AgentStatus.ERROR
    assert row.error is not None and row.error.code == "node_timeout"


async def test_a_span_that_says_nothing_is_an_error_not_a_success(database, store, worker_config):
    """What a cancelled or timed-out node looks like from here. Recording it
    as ok would be the trace claiming work that never finished."""
    run = await seed_run(database, worker_config)
    tracer = DatabaseTracer(store)

    with pytest.raises(RuntimeError):
        async with tracer.span(GraphNode.VERIFIER, research_id=run.id, iteration=1):
            raise RuntimeError("the node was torn down")

    (row,) = (await store.activity_for(run.id)).agent_runs
    assert row.status is AgentStatus.ERROR


async def test_a_step_whose_price_is_unknown_records_no_cost(database, store, worker_config):
    """Null, not zero. A run that cannot be costed must not read as free."""
    run = await seed_run(database, worker_config)
    tracer = DatabaseTracer(store)

    async with tracer.span(GraphNode.PLANNER, research_id=run.id, iteration=1) as span:
        span.succeeded(usage(cost=None))

    async with database.session() as session:
        from sqlalchemy import select

        from app.db.models.trace import AgentRunRow

        stored = (
            await session.execute(select(AgentRunRow).where(AgentRunRow.run_id == run.id))
        ).scalar_one()
        assert stored.cost_usd is None


# --- calls inside a span --------------------------------------------------


async def test_a_model_call_attaches_to_the_step_that_made_it(database, store, worker_config):
    run = await seed_run(database, worker_config)
    tracer = DatabaseTracer(store)
    recorder = DatabaseCallRecorder(store, _Silent())

    async with tracer.span(GraphNode.PLANNER, research_id=run.id, iteration=1) as span:
        await recorder.record(llm_record(run.id))
        span.succeeded(usage())

    activity = await store.activity_for(run.id)
    (call,) = activity.llm_calls
    assert call.agent_run_id == span.id
    assert (call.prompt_tokens, call.completion_tokens, call.total_tokens) == (100, 50, 150)
    assert call.prompt_version == "planner-v3"


async def test_an_unpriced_model_call_records_no_cost(database, store, worker_config):
    run = await seed_run(database, worker_config)
    recorder = DatabaseCallRecorder(store, _Silent())
    await recorder.record(llm_record(run.id, cost=None))

    async with database.session() as session:
        from sqlalchemy import select

        from app.db.models.trace import LlmCallRow

        stored = (
            await session.execute(select(LlmCallRow).where(LlmCallRow.run_id == run.id))
        ).scalar_one()
    assert stored.cost_usd is None


async def test_a_tool_call_hangs_from_the_node_that_made_it(database, store, worker_config):
    run = await seed_run(database, worker_config)
    tracer = DatabaseTracer(store)
    inner = CollectingToolRecorder()
    recorder = DatabaseToolRecorder(store, inner)

    async with tracer.span(
        GraphNode.RESEARCHER, research_id=run.id, iteration=1, task_key="pricing"
    ) as span:
        await recorder.record(tool_record(cache_hit=True))
        span.succeeded(usage())

    (call,) = (await store.activity_for(run.id)).tool_calls
    assert call.agent_run_id == span.id
    assert (call.tool_name, call.cache_hit) == (ToolName.SEARCH, True)
    assert "inference pricing" in call.request_summary
    assert len(inner.calls) == 1, "the logging recorder still saw it"


async def test_a_tool_call_outside_any_node_is_not_invented_a_parent(
    database, store, worker_config
):
    """The column is not nullable, and hanging it from a fabricated agent run
    would put a step in the trace that never happened."""
    run = await seed_run(database, worker_config)
    inner = CollectingToolRecorder()

    assert current_agent_run.get() is None
    await DatabaseToolRecorder(store, inner).record(tool_record())

    assert (await store.activity_for(run.id)).tool_calls == []
    assert len(inner.calls) == 1, "but it is still logged"


async def test_a_ledger_failure_does_not_reach_the_caller(database, store, worker_config):
    """A node whose row could not be written has still done its job."""
    recorder = DatabaseCallRecorder(_BrokenStore(), _Silent())

    await recorder.record(llm_record(uuid.uuid4()))


# --- reading it back ------------------------------------------------------


async def test_the_trace_is_scoped_to_one_run_and_ordered(database, store, worker_config):
    run = await seed_run(database, worker_config)
    other = await seed_run(database, worker_config)
    tracer = DatabaseTracer(store)
    for index, node in enumerate((GraphNode.PLANNER, GraphNode.RESEARCHER, GraphNode.CRITIC)):
        async with tracer.span(node, research_id=run.id, iteration=1) as span:
            span.succeeded(usage(tokens=10 * (index + 1)))
    async with tracer.span(GraphNode.PLANNER, research_id=other.id, iteration=1) as span:
        span.succeeded(usage())

    activity = await store.activity_for(run.id)

    assert [row.agent_name for row in activity.agent_runs] == [
        AgentName.PLANNER,
        AgentName.RESEARCHER,
        AgentName.CRITIC,
    ]
    assert all(row.run_id == run.id for row in activity.agent_runs)


async def test_the_trace_is_bounded(database, store, worker_config):
    """A pathological run must not be able to make `/activity` unloadable."""
    run = await seed_run(database, worker_config)
    tracer = DatabaseTracer(SqlAlchemyTraceStore(database))
    for _ in range(5):
        async with tracer.span(GraphNode.RESEARCHER, research_id=run.id, iteration=1) as span:
            span.succeeded(usage())

    capped = await SqlAlchemyTraceStore(database, max_rows=3).activity_for(run.id)

    assert len(capped.agent_runs) == 3


async def test_a_run_that_has_done_nothing_has_an_empty_trace(database, store, worker_config):
    run = await seed_run(database, worker_config)

    activity = await store.activity_for(run.id)

    assert (activity.agent_runs, activity.tool_calls, activity.llm_calls) == ([], [], [])


# --- a real run, and the page that shows it -------------------------------


async def test_a_run_leaves_a_step_by_step_trace(database, artifact_store, worker_config):
    """The claim Phase 16 makes: "why did the run do that?" is a query.

    Driven through the real worker and the real graph, so the spans are the
    ones the nodes actually opened rather than ones a test arranged.
    """
    run = await seed_run(database, worker_config)
    harness = build_worker(database, artifact_store, worker_config)

    await harness.queue.enqueue(run.id)
    await serve_until(
        harness, settled(harness, database, run.id, RunStatus.COMPLETED, RunStatus.FAILED)
    )

    activity = await SqlAlchemyTraceStore(database).activity_for(run.id)
    names = [row.agent_name for row in activity.agent_runs]
    assert names[0] is AgentName.PLANNER
    assert AgentName.RESEARCHER in names
    assert AgentName.SYNTHESIZER in names
    assert all(row.status is AgentStatus.OK for row in activity.agent_runs)
    assert all(row.completed_at is not None for row in activity.agent_runs)
    # The scripted agents report usage, and the ledger is where it lands.
    assert sum(row.tokens for row in activity.agent_runs) > 0


async def test_the_activity_endpoint_serves_the_run_s_own_trace(
    client, database, artifact_store, worker_config, default_user_id
):
    run = await seed_run(database, worker_config, user_id=default_user_id)
    harness = build_worker(database, artifact_store, worker_config)
    await harness.queue.enqueue(run.id)
    await serve_until(
        harness, settled(harness, database, run.id, RunStatus.COMPLETED, RunStatus.FAILED)
    )

    response = await client.get(f"{API}/research/{run.id}/activity")

    assert response.status_code == 200
    body = response.json()
    assert len(body["agent_runs"]) > 0
    assert {row["agent_name"] for row in body["agent_runs"]} <= {
        member.value for member in AgentName
    }
    assert all(row["run_id"] == str(run.id) for row in body["agent_runs"])


async def test_another_user_cannot_read_a_run_s_trace(
    client, database, artifact_store, worker_config, other_user_id
):
    """A run that is not yours is indistinguishable from one that does not
    exist - checked before a single trace row is touched."""
    run = await seed_run(database, worker_config, user_id=other_user_id)

    response = await client.get(f"{API}/research/{run.id}/activity")

    assert response.status_code == 404


class _Silent:
    """A recorder that does nothing, so a test asserts on rows alone."""

    async def record(self, call: object) -> None:
        return None


class _BrokenStore:
    """A ledger that is unavailable."""

    async def open_agent_run(self, start: object) -> None:
        raise ConnectionError("the database is unreachable")

    async def close_agent_run(self, end: object) -> None:
        raise ConnectionError("the database is unreachable")

    async def record_tool_call(
        self, call: object, *, agent_run_id: uuid.UUID, at: dt.datetime
    ) -> None:
        raise ConnectionError("the database is unreachable")

    async def record_llm_call(self, call: object, *, agent_run_id: uuid.UUID | None) -> None:
        raise ConnectionError("the database is unreachable")
