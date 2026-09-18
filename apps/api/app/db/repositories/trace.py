"""The trace tables: writing the ledger, and reading it back for `/activity`.

Implements ``app.observability.ledger.TraceStore`` and the activity read model.

**A session per write, like the worker's lifecycle writes.** These happen
inside a node, not inside a request, and a row that is not committed as it
happens is a row the trace loses when the process dies - which is precisely the
run whose trace someone will want to read. It also keeps the ledger out of the
caller's transaction: the record of what a node did must not be rolled back
because the node then failed.

**An unpriced call is null, never zero.** Both cost columns are nullable for
that reason (migration 0011). A ledger that recorded a model with no declared
price as costing nothing would make a run's total look measured when it is not,
which is the one thing the project rules say never to do.

**Every read is bounded.** A pathological run could produce thousands of tool
calls, and `/activity` is a page someone opens - so each list is capped and the
cap is declared rather than discovered.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence

from sqlalchemy import Select, select, update

from app.core.enums import (
    AgentName,
    AgentStatus,
    LlmCallStatus,
    LlmProvider,
    ToolName,
    ToolStatus,
)
from app.db.models.trace import AgentRunRow, LlmCallRow, ToolCallRow
from app.db.session import Database
from app.models.recording import LlmCallRecord
from app.observability.ledger import AgentRunEnd, AgentRunStart
from app.research.activity import ActivityResponse, AgentRunRecord
from app.research.activity import LlmCallRecord as LlmCallView
from app.research.activity import ToolCallRecord as ToolCallView
from app.research.schemas import RunError
from app.sources.base import ToolCallRecord

#: How much of a run's trace one `/activity` response carries. Generous for a
#: real run and finite for a pathological one; the counts on the run itself
#: are what a caller reconciles against.
MAX_TRACE_ROWS = 500

#: A tool request summary is a mapping; the DTO renders one line of it.
_MAX_SUMMARY_CHARS = 500


class SqlAlchemyTraceStore:
    """Writes the trace, and serves the activity page from it."""

    def __init__(self, database: Database, *, max_rows: int = MAX_TRACE_ROWS) -> None:
        self._database = database
        self._max_rows = max_rows

    # --- writes -----------------------------------------------------------

    async def open_agent_run(self, start: AgentRunStart) -> None:
        async with self._database.session() as session:
            session.add(
                AgentRunRow(
                    id=start.id,
                    run_id=start.run_id,
                    agent_name=start.agent_name.value,
                    iteration=start.iteration,
                    task_external_id=start.task_external_id,
                    status=AgentStatus.RUNNING.value,
                    summary="",
                    started_at=start.started_at,
                    trace_id=start.trace_id,
                    span_id=start.span_id,
                )
            )

    async def close_agent_run(self, end: AgentRunEnd) -> None:
        async with self._database.session() as session:
            await session.execute(
                update(AgentRunRow)
                .where(AgentRunRow.id == end.id)
                .values(
                    status=end.status.value,
                    summary=end.summary,
                    latency_ms=end.latency_ms,
                    tokens=end.tokens,
                    cost_usd=end.cost_usd,
                    error=end.error,
                    completed_at=end.completed_at,
                )
            )

    async def record_tool_call(
        self,
        call: ToolCallRecord,
        *,
        agent_run_id: uuid.UUID,
        at: dt.datetime,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        async with self._database.session() as session:
            session.add(
                ToolCallRow(
                    agent_run_id=agent_run_id,
                    tool_name=call.tool_name.value,
                    request=dict(call.request),
                    response_summary=dict(call.response_summary),
                    status=call.status.value,
                    latency_ms=call.latency_ms,
                    cache_hit=call.cache_hit,
                    retries=call.retries,
                    error=None if call.error_code is None else {"code": call.error_code},
                    started_at=at - dt.timedelta(milliseconds=call.latency_ms),
                    trace_id=trace_id,
                    span_id=span_id,
                )
            )

    async def record_llm_call(
        self,
        call: LlmCallRecord,
        *,
        agent_run_id: uuid.UUID | None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None:
        async with self._database.session() as session:
            session.add(
                LlmCallRow(
                    agent_run_id=agent_run_id,
                    run_id=call.run_id,
                    role=call.role.value,
                    provider=call.provider.value,
                    model=call.model,
                    prompt_version=call.prompt_version[:80],
                    prompt_tokens=call.prompt_tokens,
                    completion_tokens=call.completion_tokens,
                    # Null rather than zero: see the module docstring.
                    cost_usd=call.cost_usd,
                    latency_ms=call.latency_ms,
                    cache_hit=call.cache_hit,
                    status=call.status.value,
                    created_at=call.occurred_at,
                    trace_id=trace_id,
                    span_id=span_id,
                )
            )

    # --- reads ------------------------------------------------------------

    async def activity_for(self, run_id: uuid.UUID) -> ActivityResponse:
        """The run's trace: its agents, and what each of them called.

        Scoped by the run rather than by the user, like the other projections:
        the caller has already established ownership (``ResearchService``), and
        this join is what makes the tool calls the *run's* rather than every
        tool call in the system.
        """
        async with self._database.session() as session:
            agents = (await session.execute(self._agents_query(run_id))).scalars().all()
            tools = (await session.execute(self._tools_query(run_id))).scalars().all()
            calls = (await session.execute(self._calls_query(run_id))).scalars().all()

        return ActivityResponse(
            agent_runs=[_agent_view(row) for row in agents],
            tool_calls=[_tool_view(row) for row in tools],
            llm_calls=[_call_view(row) for row in calls],
        )

    def _agents_query(self, run_id: uuid.UUID) -> Select[tuple[AgentRunRow]]:
        return (
            select(AgentRunRow)
            .where(AgentRunRow.run_id == run_id)
            .order_by(AgentRunRow.started_at, AgentRunRow.id)
            .limit(self._max_rows)
        )

    def _tools_query(self, run_id: uuid.UUID) -> Select[tuple[ToolCallRow]]:
        return (
            select(ToolCallRow)
            .join(AgentRunRow, AgentRunRow.id == ToolCallRow.agent_run_id)
            .where(AgentRunRow.run_id == run_id)
            .order_by(ToolCallRow.started_at, ToolCallRow.id)
            .limit(self._max_rows)
        )

    def _calls_query(self, run_id: uuid.UUID) -> Select[tuple[LlmCallRow]]:
        return (
            select(LlmCallRow)
            .where(LlmCallRow.run_id == run_id)
            .order_by(LlmCallRow.created_at, LlmCallRow.id)
            .limit(self._max_rows)
        )


def _agent_view(row: AgentRunRow) -> AgentRunRecord:
    return AgentRunRecord(
        id=row.id,
        run_id=row.run_id,
        task_external_id=row.task_external_id,
        agent_name=AgentName(row.agent_name),
        iteration=row.iteration,
        status=AgentStatus(row.status),
        summary=row.summary,
        latency_ms=row.latency_ms,
        tokens=row.tokens,
        # The DTO's `cost_usd` is not nullable, and a step with an unpriced
        # call reports 0.0 there. The honest signal is the run's own
        # `uncosted_calls`; this field is a per-step breakdown, not the total.
        cost_usd=float(row.cost_usd or 0.0),
        trace_id=row.trace_id,
        span_id=row.span_id,
        error=None if row.error is None else RunError.model_validate(row.error),
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def _tool_view(row: ToolCallRow) -> ToolCallView:
    return ToolCallView(
        id=row.id,
        agent_run_id=row.agent_run_id,
        tool_name=ToolName(row.tool_name),
        request_summary=_summarise(row.request),
        status=ToolStatus(row.status),
        latency_ms=row.latency_ms,
        cache_hit=row.cache_hit,
        retries=row.retries,
        result_summary=_summarise(row.response_summary),
        started_at=row.started_at,
    )


def _call_view(row: LlmCallRow) -> LlmCallView:
    return LlmCallView(
        id=row.id,
        agent_run_id=row.agent_run_id,
        role=AgentName(row.role),
        provider=LlmProvider(row.provider),
        model=row.model,
        prompt_version=row.prompt_version,
        prompt_tokens=row.prompt_tokens,
        completion_tokens=row.completion_tokens,
        total_tokens=row.prompt_tokens + row.completion_tokens,
        cost_usd=float(row.cost_usd or 0.0),
        latency_ms=row.latency_ms,
        cache_hit=row.cache_hit,
        status=LlmCallStatus(row.status),
        created_at=row.created_at,
    )


def _summarise(payload: dict[str, object]) -> str:
    """A jsonb blob as the one line the activity feed shows.

    Not the whole object: the feed is a timeline, and a request summary that
    is three hundred characters of JSON is a row nobody can scan.
    """
    if not payload:
        return ""
    parts: Sequence[str] = [f"{key}={value}" for key, value in payload.items()]
    return ", ".join(parts)[:_MAX_SUMMARY_CHARS]
