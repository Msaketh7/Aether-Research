"""Running the research graph for one run: start it, or resume it where it stopped.

The worker (Phase 13) owns picking runs up, retrying them and recording their
outcome. This owns what happens once one is picked up, and makes one promise:
calling ``run`` again for the same run resumes it rather than repeating it. The
checkpoint thread is the run's id, so a second call - after a crash, a deploy
or a raised failure - finds the last checkpoint and continues from the node
that had not finished. Nodes that completed are not called again.

It reports each superstep to a ``StepListener`` on the way past, which is how
the worker (Phase 13) keeps a run's row moving: LangGraph is streamed rather than
awaited, and every value chunk is the accumulated state after one superstep while
the update chunk before it names the nodes that produced it. A listener that
raises stops the run - that is deliberate, and it is how a worker whose lease has
expired stops touching a run that is no longer its own.

It also hands the finished state to a ``ResultRecorder`` (Phase 11), because a
checkpoint is readable only by the graph: until its claims, spans and
contradictions are projected onto rows, a run that succeeded has nothing to show
anyone. That happens on the way out of a failure too - a run that died at
synthesis still found sources and quoted spans - and the recorder is asked once
per call, so a resumed run re-projects what it already had, which the derived
ids make a rewrite rather than a duplicate.

Four settings are made per invocation, each for a stated reason:

* ``durability="sync"``: a node's checkpoint is written before the next step
  starts. The cheaper modes can lose the last step on a crash, and the last
  step of a research run is usually the most expensive one.
* ``recursion_limit`` from the run's shape (``budget.recursion_limit``):
  LangGraph's default of 25 would end a four-round deep run with an exception.
* ``max_concurrency`` from settings: how many researchers run at once.
* LangSmith tracing **off unless a deployment asked for it**, and configured
  by handing this runner a client rather than by setting the variable the
  library reads. Prompts and retrieved documents would otherwise leave the
  system for a third party whenever ``LANGSMITH_TRACING`` happened to be set
  in a shell (Phase 9's finding). No client means ``enabled=False``, which is
  what every test and every default deployment gets.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Iterator
from contextlib import aclosing
from datetime import datetime
from typing import Any, Protocol, cast
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import StateSnapshot
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
from app.agents.schemas import GraphNode
from app.agents.state import ResearchState, RunBrief, initial_state
from app.agents.tracing import AgentTracer, NullTracer
from app.core.enums import ResearchMode
from app.core.logging import get_logger

logger = get_logger(__name__)


def thread_id(research_id: UUID) -> str:
    """The checkpoint thread for a run. Derived, so it never needs looking up."""
    return str(research_id)


def _graph_nodes(update: object) -> Iterator[GraphNode]:
    """The research nodes named by one ``updates`` chunk.

    LangGraph's own bookkeeping keys - ``__start__`` and the rest - are not
    nodes anyone asked for, so they are skipped rather than raising: a library
    upgrade that adds one must not stop a run.
    """
    if not isinstance(update, dict):  # pragma: no cover - defensive
        return
    for name in update:
        try:
            yield GraphNode(name)
        except ValueError:
            continue


class ResultRecorder(Protocol):
    """What the runner does with a finished run's state besides return it.

    The graph's own memory is its checkpoint, which only the graph can read. A
    recorder is what turns that into the rows the product is served from
    (``app.evidence.projection``). Declared as a protocol here so the runner
    depends on the act, not on the database - and so a test can watch it.
    """

    async def record(self, state: ResearchState) -> object: ...


class StepListener(Protocol):
    """Told what the graph did, one superstep at a time, while it runs.

    ``nodes`` is what ran in that step - more than one only where the graph fans
    out, which is the researchers - and ``state`` is the whole state after it.
    Raising from here ends the run, so a listener is also the way a caller stops
    a graph it has lost the right to run.
    """

    async def stepped(self, nodes: tuple[GraphNode, ...], state: ResearchState) -> None: ...


class ResearchGraphRunner:
    """Starts and resumes research graphs. One per worker process."""

    def __init__(
        self,
        *,
        nodes: ResearchNodes,
        checkpointer: BaseCheckpointSaver[Any],
        probe: CancellationProbe,
        bounds: GraphBounds,
        recorder: ResultRecorder,
        now: Clock = utcnow,
        tracer: AgentTracer | None = None,
        langsmith: Any | None = None,
        langsmith_project: str | None = None,
    ) -> None:
        self._nodes = nodes
        self._checkpointer = checkpointer
        self._probe = probe
        self._bounds = bounds
        self._recorder = recorder
        self._now = now
        # One tracer for every run this process executes, like the graphs it
        # builds: a span is opened per node execution, not per runner.
        self._tracer: AgentTracer = tracer or NullTracer()
        # A client, or None. Passed to the library per invocation rather
        # than left to whatever it reads out of the environment, so there is
        # one place the decision is made. See the module notes.
        self._langsmith = langsmith
        self._langsmith_project = langsmith_project
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
                tracer=self._tracer,
            )
        return self._graphs[mode]

    async def run(self, brief: RunBrief, *, listener: StepListener | None = None) -> ResearchState:
        """Run to completion, resuming from the last checkpoint if there is one.

        Raises a ``GraphError`` when the run cannot produce a report. Calling
        again afterwards resumes at the node that failed.

        The listener is per call rather than per runner: one runner serves every
        run a worker executes, and what a listener does - renew *this* run's
        lease, move *this* run's row - belongs to one of them.
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
        try:
            with tracing_context(
                enabled=self._langsmith is not None,
                client=self._langsmith,
                project_name=self._langsmith_project,
            ):
                final = await self._stream(
                    graph, payload, config, started=started, listener=listener
                )
        except Exception:
            # A run that failed at synthesis still found sources and quoted
            # spans, and a person looking at why it failed needs to see them.
            # Not ``BaseException``: a cancelled task is the process being torn
            # down, and starting two more awaits inside it would delay the
            # teardown to write rows the next start will write anyway.
            await self._record_last_checkpoint(graph, config, research_id=brief.research_id)
            raise

        # Unguarded, unlike the failure path: a run whose results could not be
        # stored has not finished, and the worker should see that and retry.
        await self._recorder.record(final)
        return final

    async def _stream(
        self,
        graph: ResearchGraph,
        payload: ResearchState | None,
        config: RunnableConfig,
        *,
        started: datetime,
        listener: StepListener | None,
    ) -> ResearchState:
        """Run the graph, reporting each superstep, and return the final state.

        Two stream modes rather than one: ``updates`` names the nodes that ran,
        ``values`` carries the state they produced, and LangGraph emits them in
        that order per superstep. ``values`` alone could not say which node ran;
        ``updates`` alone carries each node's own return value, not the reduced
        state, so the counts a listener wants would have to be re-derived.

        The stream is closed explicitly. A listener that raises - a worker whose
        lease has gone - leaves the loop early, and without this the generator
        would only be finalised whenever it was collected.
        """
        final: ResearchState | None = None
        stepped: list[GraphNode] = []
        # LangGraph declares an ``AsyncIterator`` and returns an async
        # generator. The difference only matters for closing it, which is
        # exactly what this is for, so the cast is the narrow claim it looks
        # like: a version that stopped returning one would fail here and in
        # every graph test at once.
        stream = cast(
            AsyncGenerator[Any, None],
            graph.astream(
                payload,
                config,
                context=GraphContext(resumed_at=started),
                durability="sync",
                stream_mode=["updates", "values"],
            ),
        )
        async with aclosing(stream):
            async for mode, chunk in stream:
                if mode == "updates":
                    stepped.extend(_graph_nodes(chunk))
                    continue
                final = cast(ResearchState, chunk)
                # The first values chunk is the input, before any node has run,
                # so there is nothing to report for it.
                if stepped and listener is not None:
                    await listener.stepped(tuple(stepped), final)
                stepped.clear()

        if final is None:  # pragma: no cover - a stream always yields its input
            snapshot: StateSnapshot = await graph.aget_state(config)
            final = cast(ResearchState, snapshot.values)
        return final

    async def _record_last_checkpoint(
        self, graph: ResearchGraph, config: RunnableConfig, *, research_id: UUID
    ) -> None:
        """Project a failed run's last checkpoint, without masking the failure.

        The checkpoint is already durable, so reading it back costs one query and
        gives the recorder exactly what a resume would start from. Every failure
        in here is swallowed: the exception on its way up is why the run stopped,
        and replacing it - with a projection error, or with the database error
        that may well have been the original cause - would lose the diagnosis and
        change what the worker retries.
        """
        try:
            snapshot: StateSnapshot = await graph.aget_state(config)
            if not snapshot.values:
                return
            await self._recorder.record(cast(ResearchState, snapshot.values))
        except Exception:
            logger.exception(
                "could not record the evidence of a failed run",
                extra={"research_id": str(research_id)},
            )
