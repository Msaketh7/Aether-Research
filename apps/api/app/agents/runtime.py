"""Running the research graph for one run: start it, or resume it where it stopped.

The worker (Phase 13) owns picking runs up, retrying them and recording their
outcome. This owns what happens once one is picked up, and makes one promise:
calling ``run`` again for the same run resumes it rather than repeating it. The
checkpoint thread is the run's id, so a second call - after a crash, a deploy
or a raised failure - finds the last checkpoint and continues from the node
that had not finished. Nodes that completed are not called again.

Four settings are made per invocation, each for a stated reason:

* ``durability="sync"``: a node's checkpoint is written before the next step
  starts. The cheaper modes can lose the last step on a crash, and the last
  step of a research run is usually the most expensive one.
* ``recursion_limit`` from the run's shape (``budget.recursion_limit``):
  LangGraph's default of 25 would end a four-round deep run with an exception.
* ``max_concurrency`` from settings: how many researchers run at once.
* LangSmith tracing **off**. Prompts and retrieved documents would otherwise
  leave the system for a third party whenever ``LANGSMITH_TRACING`` happened to
  be set in the environment - read by the library directly, past the typed
  settings layer. Turning tracing on is Phase 17's decision to make on purpose.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langsmith.run_helpers import tracing_context

from app.agents import budget as governor
from app.agents.graph import (
    CancellationProbe,
    Clock,
    GraphBounds,
    GraphContext,
    ResearchGraph,
    build_research_graph,
    utcnow,
)
from app.agents.nodes import ResearchNodes
from app.agents.state import ResearchState, RunBrief, initial_state
from app.core.enums import ResearchMode


def thread_id(research_id: UUID) -> str:
    """The checkpoint thread for a run. Derived, so it never needs looking up."""
    return str(research_id)


class ResearchGraphRunner:
    """Starts and resumes research graphs. One per worker process."""

    def __init__(
        self,
        *,
        nodes: ResearchNodes,
        checkpointer: BaseCheckpointSaver[Any],
        probe: CancellationProbe,
        bounds: GraphBounds,
        now: Clock = utcnow,
    ) -> None:
        self._nodes = nodes
        self._checkpointer = checkpointer
        self._probe = probe
        self._bounds = bounds
        self._now = now
        self._graphs: dict[ResearchMode, ResearchGraph] = {}

    def graph(self, mode: ResearchMode) -> ResearchGraph:
        """The compiled graph for a mode, built once and shared across runs."""
        if mode not in self._graphs:
            self._graphs[mode] = build_research_graph(
                self._nodes,
                mode=mode,
                probe=self._probe,
                bounds=self._bounds,
                checkpointer=self._checkpointer,
                now=self._now,
            )
        return self._graphs[mode]

    async def run(self, brief: RunBrief) -> ResearchState:
        """Run to completion, resuming from the last checkpoint if there is one.

        Raises a ``GraphError`` when the run cannot produce a report. Calling
        again afterwards resumes at the node that failed.
        """
        graph = self.graph(brief.mode)
        config: RunnableConfig = {
            "configurable": {"thread_id": thread_id(brief.research_id)},
            "recursion_limit": governor.recursion_limit(
                brief.mode, max_iterations=brief.budget.max_iterations
            ),
            "max_concurrency": self._bounds.max_concurrency,
        }
        started = self._now()
        saved = await graph.aget_state(config)
        # A run with a checkpoint resumes from it, and its frozen budget and
        # question come from the checkpoint rather than from this brief.
        payload = None if saved.values else initial_state(brief, now=started)
        with tracing_context(enabled=False):
            final = await graph.ainvoke(
                payload,
                config,
                context=GraphContext(resumed_at=started),
                durability="sync",
            )
        return cast(ResearchState, final)
