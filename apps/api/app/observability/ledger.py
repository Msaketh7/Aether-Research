"""The trace as it is written and read: agent runs, tool calls, model calls.

The project rule has been in force since Phase 5 - *record every LLM call and
tool call: tokens, cost, latency, status* - and until now it was satisfied by
structured logs, because `llm_calls` and `tool_calls` hang from an `agent_run`
and nothing created one. This is the layer that does, and it is what makes
`/activity` and every cost question answerable from rows rather than from a log
aggregator (FR-10, Phase 16).

Three ideas hold it together.

**A node execution is the unit.** One `agent_runs` row per visit to a node,
with the round it belongs to and - for a researcher - the subtask it was given.
Everything that node spends hangs from that row, which is what makes "which
step spent the money" a query rather than an investigation.

**The current span travels in a context variable.** A tool call is made deep
inside a researcher, through a toolbelt that is shared by the whole process and
knows nothing about runs; a model call goes through a gateway with the same
shape. Threading an id through both would mean changing every tool signature
and every agent, for a value that is genuinely ambient: it is *where we are*,
and `contextvars` is the mechanism the language provides for that. Each
researcher runs in its own task, so each gets its own copy.

**A row carries the trace it happened in.** ``trace_id`` and ``span_id``
have been columns since Phase 3 and empty until Phase 17. Filled from whatever
span is active - ``None`` when nothing is recording, which is the normal state
for a deployment with no collector - they are what connects a row saying a
step cost forty cents to the trace showing what it did for eleven seconds.

**Recording never fails the work.** Every write here is wrapped: a node whose
ledger row could not be written has still done its job, and a research run that
failed because its telemetry did is a worse outcome by a wide margin. The one
thing that is *not* silent is the log line saying the record was lost.
"""

from __future__ import annotations

import contextvars
import datetime as dt
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from app.agents.schemas import GraphNode, NodeError, NodeUsage
from app.agents.tracing import AgentSpan
from app.core.enums import AgentName, AgentStatus
from app.core.logging import get_logger
from app.models.recording import CallRecorder, LlmCallRecord
from app.observability.tracing import current_ids
from app.sources.base import CallRecorder as ToolCallRecorder
from app.sources.base import ToolCallRecord

logger = get_logger(__name__)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


#: Which agent each node is, for the `agent_runs.agent_name` vocabulary. The
#: contradiction checker is the critic's work by another name - the enum is the
#: closed list the column constrains, and inventing a value to match a node that
#: is not its own agent would change a contract the frontend reads.
#:
#: **It has to be total.** A node missing from here raises a ``KeyError`` inside
#: a guarded write, so the step runs, succeeds, and leaves no trace row - and
#: the model call it made hangs from nothing and is dropped too. The answerer
#: shipped that way for exactly as long as it took a scenario to count the
#: ledger's rows against the calls the run actually made.
NODE_AGENT: dict[GraphNode, AgentName] = {
    GraphNode.PLANNER: AgentName.PLANNER,
    GraphNode.RESEARCHER: AgentName.RESEARCHER,
    GraphNode.EVIDENCE_EXTRACTOR: AgentName.EVIDENCE_EXTRACTOR,
    GraphNode.CLAIM_NORMALIZER: AgentName.CLAIM_NORMALIZER,
    GraphNode.VERIFIER: AgentName.VERIFIER,
    GraphNode.CONTRADICTION_CHECKER: AgentName.CRITIC,
    GraphNode.CRITIC: AgentName.CRITIC,
    GraphNode.ANSWERER: AgentName.ANSWERER,
    GraphNode.SYNTHESIZER: AgentName.SYNTHESIZER,
    GraphNode.CITATION_VALIDATOR: AgentName.CITATION_VALIDATOR,
}

#: The span the current task is inside, or ``None`` outside a node. Read by the
#: two recorders below so a call attaches to the step that made it.
current_agent_run: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar(
    "aether_current_agent_run", default=None
)


@dataclass(frozen=True, slots=True)
class AgentRunStart:
    """A node execution, as it begins."""

    id: uuid.UUID
    run_id: uuid.UUID
    agent_name: AgentName
    iteration: int
    task_external_id: str | None
    started_at: dt.datetime
    #: The trace this step happened in, or ``None`` when nothing is recording.
    trace_id: str | None = None
    span_id: str | None = None


@dataclass(frozen=True, slots=True)
class AgentRunEnd:
    """A node execution, as it ends."""

    id: uuid.UUID
    status: AgentStatus
    summary: str
    latency_ms: int
    tokens: int
    #: ``None`` when a call in this node could not be priced. Never 0.0, which
    #: is what a free call costs.
    cost_usd: float | None
    error: dict[str, Any] | None
    completed_at: dt.datetime


class TraceStore(Protocol):
    """Where the trace is written. Implemented in ``app.db.repositories.trace``."""

    async def open_agent_run(self, start: AgentRunStart) -> None: ...

    async def close_agent_run(self, end: AgentRunEnd) -> None: ...

    async def record_tool_call(
        self,
        call: ToolCallRecord,
        *,
        agent_run_id: uuid.UUID,
        at: dt.datetime,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None: ...

    async def record_llm_call(
        self,
        call: LlmCallRecord,
        *,
        agent_run_id: uuid.UUID | None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> None: ...


#: Injected so a test can pin the timestamps a span writes.
type Clock = Callable[[], dt.datetime]


class DatabaseAgentSpan:
    """One node execution, held open while the node runs."""

    def __init__(self, *, row_id: uuid.UUID, started: dt.datetime) -> None:
        self.row_id = row_id
        self.started_at = started
        self.outcome: AgentStatus | None = None
        self.summary = ""
        self.usage = NodeUsage()
        self.error: dict[str, Any] | None = None

    @property
    def id(self) -> uuid.UUID | None:
        return self.row_id

    def succeeded(self, usage: NodeUsage, *, summary: str = "") -> None:
        self.outcome = AgentStatus.OK
        self.usage = usage
        self.summary = summary[:2000]

    def failed(self, error: NodeError) -> None:
        self.outcome = AgentStatus.ERROR
        self.summary = error.message[:2000]
        self.error = {"code": error.code, "message": error.message}


class DatabaseTracer:
    """Opens an ``agent_runs`` row per node execution, and closes it."""

    def __init__(self, store: TraceStore, *, now: Clock = _utcnow) -> None:
        self._store = store
        self._now = now

    @asynccontextmanager
    async def span(
        self,
        node: GraphNode,
        *,
        research_id: uuid.UUID,
        iteration: int,
        task_key: str | None = None,
    ) -> AsyncIterator[AgentSpan]:
        started = self._now()
        span = DatabaseAgentSpan(row_id=uuid.uuid4(), started=started)
        ids = current_ids()
        await self._guarded(
            lambda: self._store.open_agent_run(
                AgentRunStart(
                    id=span.row_id,
                    run_id=research_id,
                    agent_name=NODE_AGENT[node],
                    # A researcher dispatched before the first plan exists would
                    # be a bug, but the column is >= 1 and a trace row must not
                    # be what fails the run.
                    iteration=max(1, iteration),
                    task_external_id=task_key,
                    started_at=started,
                    trace_id=ids.trace_id,
                    span_id=ids.span_id,
                )
            ),
            "could not open an agent run",
        )
        token = current_agent_run.set(span.row_id)
        try:
            yield span
        finally:
            current_agent_run.reset(token)
            finished = self._now()
            await self._guarded(
                lambda: self._store.close_agent_run(
                    AgentRunEnd(
                        id=span.row_id,
                        # A span that closed without either call is a node that
                        # was cancelled or timed out. Recording it as ok would
                        # be the trace claiming work that did not finish.
                        status=span.outcome or AgentStatus.ERROR,
                        summary=span.summary,
                        latency_ms=int((finished - started).total_seconds() * 1000),
                        tokens=span.usage.tokens.total,
                        cost_usd=(
                            None
                            if span.usage.cost.uncosted_calls
                            else round(span.usage.cost.usd, 6)
                        ),
                        error=span.error,
                        completed_at=finished,
                    )
                ),
                "could not close an agent run",
            )

    async def _guarded(self, write: Callable[[], Awaitable[None]], message: str) -> None:
        try:
            await write()
        except Exception as exc:
            logger.warning(message, extra={"error": str(exc)})


class DatabaseCallRecorder:
    """Writes `llm_calls`, attributed to whichever node is running.

    Wraps another recorder rather than replacing it, so the structured log line
    that Phases 5 to 15 relied on keeps being written: a row is queryable and a
    log line is greppable, and losing either would be a step backwards.
    """

    def __init__(self, store: TraceStore, inner: CallRecorder) -> None:
        self._store = store
        self._inner = inner

    async def record(self, call: LlmCallRecord) -> None:
        await self._inner.record(call)
        ids = current_ids()
        try:
            await self._store.record_llm_call(
                call,
                agent_run_id=current_agent_run.get(),
                trace_id=ids.trace_id,
                span_id=ids.span_id,
            )
        except Exception as exc:
            logger.warning(
                "an llm call could not be written to the ledger",
                extra={"error": str(exc), "run_id": str(call.run_id) if call.run_id else None},
            )


class DatabaseToolRecorder:
    """Writes `tool_calls`, hung from the node that made them.

    A call made with no node in scope is logged and not stored: the column is
    not nullable, and inventing an agent run to hang it from would put a step
    in the trace that never happened.
    """

    def __init__(self, store: TraceStore, inner: ToolCallRecorder) -> None:
        self._store = store
        self._inner = inner

    async def record(self, call: ToolCallRecord) -> None:
        await self._inner.record(call)
        agent_run_id = current_agent_run.get()
        if agent_run_id is None:
            logger.debug(
                "a tool call was made outside any agent run",
                extra={"tool": call.tool_name.value},
            )
            return
        ids = current_ids()
        try:
            await self._store.record_tool_call(
                call,
                agent_run_id=agent_run_id,
                at=_utcnow(),
                trace_id=ids.trace_id,
                span_id=ids.span_id,
            )
        except Exception as exc:
            logger.warning(
                "a tool call could not be written to the ledger",
                extra={"error": str(exc), "tool": call.tool_name.value},
            )
