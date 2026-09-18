"""What the graph tells the ledger while it runs (Phase 16).

The project rule is unconditional: *record every LLM call and tool call -
tokens, cost, latency, status*. Phases 5 and 6 built the recorders and wrote
structured logs, because `llm_calls` and `tool_calls` hang from an `agent_run`
and no agent existed yet to hang one from. Phases 9 and 10 built the agents.
This is the seam that connects them.

**A span is one node execution, not one agent.** The graph runs nine nodes and
a deep run visits most of them several times; each visit is a row, with its
round and - for a researcher - the subtask it was given. That is what makes
"which step spent the money" answerable, and it is the shape `/activity`
renders.

**The graph depends on the act, not on the database.** ``AgentTracer`` is a
protocol with a null implementation, so a graph test needs no Postgres and the
worker's real tracer is one object passed in at the top. Nothing in the nodes
knows a table exists.

**A span never fails a run.** The ledger is a record of the work, not part of
it: a node whose row could not be written has still done its job, and losing
the trace is the lesser failure by a wide margin. Implementations swallow and
log; this module's contract says so out loud so that no implementation is
tempted to be stricter.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol

from app.agents.schemas import GraphNode, NodeError, NodeUsage


class AgentSpan(Protocol):
    """One node execution, as the ledger sees it.

    Exactly one of ``succeeded`` or ``failed`` is called before the span
    closes; a span that closes without either is recorded as an error, which
    is what a cancelled or timed-out node looks like from here.
    """

    @property
    def id(self) -> uuid.UUID | None:
        """The ``agent_runs`` row this span writes to, if it has one.

        ``None`` for a tracer that stores nothing. Tool and model calls made
        inside the span attach to it by this id.
        """
        ...

    def succeeded(self, usage: NodeUsage, *, summary: str = "") -> None: ...

    def failed(self, error: NodeError) -> None: ...


class AgentTracer(Protocol):
    """Opens a span per node execution."""

    def span(
        self,
        node: GraphNode,
        *,
        research_id: uuid.UUID,
        iteration: int,
        task_key: str | None = None,
    ) -> AbstractAsyncContextManager[AgentSpan]:
        """An async context manager around one node execution.

        Declared as the context manager rather than as the generator it is
        usually written with, because that is what the caller uses it as -
        and a protocol that promised an iterator would not typecheck at the
        one call site there is.
        """
        ...


class NullSpan:
    """A span that records nothing. The default, and what a test uses."""

    @property
    def id(self) -> uuid.UUID | None:
        return None

    def succeeded(self, usage: NodeUsage, *, summary: str = "") -> None:
        return None

    def failed(self, error: NodeError) -> None:
        return None


class NullTracer:
    """No ledger. The graph behaves identically, which is the point."""

    @asynccontextmanager
    async def span(
        self,
        node: GraphNode,
        *,
        research_id: uuid.UUID,
        iteration: int,
        task_key: str | None = None,
    ) -> AsyncIterator[AgentSpan]:
        yield NullSpan()
