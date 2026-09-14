"""The research graph: its shape, its routing, and the governance around every node.

Phase 9, TDD 4, ADR 0002, ADR 0014.

Every node is wrapped, and the wrapper - not the agent - is where the system's
guarantees live, so an agent written in Phase 10 cannot opt out of them:

* **At entry** the cancel flag is read, and a discovery node checks the FR-8
  ceilings (``app.agents.budget``). A node that should not run returns without
  calling its agent.
* **Around the call**, a timeout. No node, and no single researcher, can hold a
  run past ``graph_node_timeout_seconds``; a researcher is also held to what is
  left of the run's runtime.
* **At exit**, the node's reported usage is added to the run's totals and the
  run clock advances.

What a failure does depends on what the run can still produce::

    researcher                        recorded in failed_tasks; the round goes on
    evidence, claims, verification,
    contradictions                    recorded in errors; the run goes on
    the critic, or a later planning   recorded; discovery ends, synthesis runs
    the first planning round          raised: there is nothing to research
    synthesis, citation validation    raised: there is no report to return
    a broken node contract            raised: a bug, not something to route around

LangGraph's node retry is not used. Model calls are already retried by the
gateway and tool calls by the toolbelt; retrying a whole node on top would
repeat every successful call inside it and pay for each one twice.

A quick run keeps evidence extraction and claim normalization, which the TDD's
trimmed graph omits (section 4.3): a citation resolves through a claim to its
evidence, so a quick report without them could only cite nothing. It skips
verification, the contradiction check and the critic, as PRD 5.1 specifies.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import get_runtime
from langgraph.types import Send

from app.agents import budget as governor
from app.agents.errors import (
    CitationValidationFailed,
    NodeContractViolated,
    PlanningFailed,
    SynthesisFailed,
)
from app.agents.nodes import NodeResult, ResearchNodes
from app.agents.schemas import (
    ERROR_CODE_PATTERN,
    CitationCheck,
    ClaimItem,
    ContradictionItem,
    Critique,
    EvidenceItem,
    GraphNode,
    NodeError,
    NodeUsage,
    Plan,
    ReportDraft,
    RunClock,
    Stop,
    StopReason,
    SubtaskAssignment,
    TaskOutcome,
)
from app.agents.state import ResearchState, consumption, stop_reason
from app.core.config import Settings
from app.core.enums import ResearchMode
from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger(__name__)

Clock = Callable[[], datetime]
Update = dict[str, Any]
#: A compiled research graph. LangGraph's generic parameters add nothing a
#: caller can use, so they are not spelled out.
ResearchGraph = CompiledStateGraph[Any, Any, Any, Any]

_ERROR_CODE = re.compile(ERROR_CODE_PATTERN)
#: Raised through a node's failure handling untouched: LangGraph's own control
#: flow, and contract violations, which are bugs rather than failures.
_PROPAGATE: tuple[type[BaseException], ...] = (GraphBubbleUp, NodeContractViolated)

_PLANNER = GraphNode.PLANNER.value
_RESEARCHER = GraphNode.RESEARCHER.value
_EVIDENCE = GraphNode.EVIDENCE_EXTRACTOR.value
_NORMALIZER = GraphNode.CLAIM_NORMALIZER.value
_VERIFIER = GraphNode.VERIFIER.value
_CONTRADICTIONS = GraphNode.CONTRADICTION_CHECKER.value
_CRITIC = GraphNode.CRITIC.value
_SYNTHESIZER = GraphNode.SYNTHESIZER.value
_VALIDATOR = GraphNode.CITATION_VALIDATOR.value


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class GraphContext:
    """Per-invocation facts that must not be checkpointed.

    ``resumed_at`` is when this process picked the run up. It is what keeps a
    crashed worker's downtime out of the runtime ceiling (see ``RunClock``),
    and it differs on every invocation by definition - so it travels with the
    invocation, not in the state.
    """

    resumed_at: datetime


class CancellationProbe(Protocol):
    """Whether a run has been cancelled. Read at every node boundary (TDD 11)."""

    async def is_cancelled(self, research_id: UUID, user_id: UUID) -> bool: ...


@dataclass(frozen=True, slots=True)
class GraphBounds:
    """The graph's own bounds, beside each run's budget."""

    max_subtasks_per_iteration: int
    max_concurrency: int
    node_timeout_seconds: float

    @classmethod
    def from_settings(cls, settings: Settings) -> GraphBounds:
        return cls(
            max_subtasks_per_iteration=settings.max_subtasks_per_iteration,
            max_concurrency=settings.graph_max_concurrency,
            node_timeout_seconds=settings.graph_node_timeout_seconds,
        )


def build_research_graph(
    nodes: ResearchNodes,
    *,
    mode: ResearchMode,
    probe: CancellationProbe,
    bounds: GraphBounds,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    now: Clock = utcnow,
) -> ResearchGraph:
    """The graph for one mode. Deep and conversational runs share a shape."""
    governed = _Governed(nodes, probe=probe, bounds=bounds, now=now)
    graph = StateGraph(ResearchState, context_schema=GraphContext)

    graph.add_node(_PLANNER, governed.planner)
    graph.add_node(_RESEARCHER, governed.researcher)
    graph.add_node(_EVIDENCE, governed.evidence_extractor)
    graph.add_node(_NORMALIZER, governed.claim_normalizer)
    graph.add_node(_SYNTHESIZER, governed.synthesizer)
    graph.add_node(_VALIDATOR, governed.citation_validator)

    graph.add_edge(START, _PLANNER)
    graph.add_conditional_edges(_PLANNER, governed.after_planner, [_RESEARCHER, _SYNTHESIZER, END])
    graph.add_edge(_RESEARCHER, _EVIDENCE)
    graph.add_edge(_EVIDENCE, _NORMALIZER)

    if mode is ResearchMode.QUICK:
        graph.add_edge(_NORMALIZER, _SYNTHESIZER)
    else:
        graph.add_node(_VERIFIER, governed.verifier)
        graph.add_node(_CONTRADICTIONS, governed.contradiction_checker)
        graph.add_node(_CRITIC, governed.critic)
        graph.add_edge(_NORMALIZER, _VERIFIER)
        graph.add_edge(_VERIFIER, _CONTRADICTIONS)
        graph.add_edge(_CONTRADICTIONS, _CRITIC)
        graph.add_conditional_edges(_CRITIC, governed.after_critic, [_PLANNER, _SYNTHESIZER, END])

    graph.add_edge(_SYNTHESIZER, _VALIDATOR)
    graph.add_conditional_edges(_VALIDATOR, governed.after_validation, [_SYNTHESIZER, END])
    return graph.compile(checkpointer=checkpointer, name=f"research-{mode.value}")


class _Governed:
    """The nodes as the graph runs them: each agent call inside the run's rules."""

    def __init__(
        self,
        nodes: ResearchNodes,
        *,
        probe: CancellationProbe,
        bounds: GraphBounds,
        now: Clock,
    ) -> None:
        self._nodes = nodes
        self._probe = probe
        self._bounds = bounds
        self._now = now

    # --- discovery ---------------------------------------------------------------

    async def planner(self, state: ResearchState) -> Update:
        now = self._now()
        if await self._cancelled(state):
            return self._cancel(state, now)
        if state.get("stop") is not None:
            return self._accounted(state, now)
        budget = state["budget"]
        used = self._used(state, now)
        reason = governor.limit_reached(budget, used)
        if reason is None and not governor.may_plan_again(budget, used):
            reason = StopReason.ITERATIONS
        if reason is not None:
            return {
                **self._accounted(state, now),
                **self._stop(state, reason, used, critique=state.get("critique")),
            }

        iteration = state.get("iteration", 0) + 1
        try:
            result = await self._call(lambda: self._nodes.planner.plan(state))
            plan = _expect(GraphNode.PLANNER, result.value, Plan)
        except _PROPAGATE:
            raise
        except Exception as exc:
            if iteration == 1:
                raise PlanningFailed(context={"research_id": str(state["research_id"])}) from exc
            now = self._now()
            return {
                **self._accounted(state, now),
                **self._stop(state, StopReason.DISCOVERY_FAILED, self._used(state, now)),
                "errors": [self._failure(GraphNode.PLANNER, iteration, exc)],
            }
        if plan.iteration != iteration:
            raise NodeContractViolated(
                context={"node": _PLANNER, "expected": iteration, "returned": plan.iteration}
            )

        now = self._now()
        update: Update = {
            "iteration": iteration,
            "research_plan": plan,
            "subtasks": list(plan.subtasks),
            **self._accounted(state, now, result.usage),
        }
        # Checked again after planning: the plan itself took time and money, and
        # a round must not be dispatched with neither left to spend.
        used = self._used(state, now, result.usage)
        reason = governor.limit_reached(budget, used, at_round_boundary=False)
        if reason is not None:
            update.update(self._stop(state, reason, used))
        return update

    async def researcher(self, state: Any) -> Update:
        # LangGraph calls every node with one input named `state`; for this node
        # that input is the Send argument, a SubtaskAssignment. It is typed as
        # Any on purpose: LangGraph infers a node's input channels from the
        # annotation, and naming the model would add its fields as channels
        # beside the state's own. It is checked on the next line instead.
        work = _expect(GraphNode.RESEARCHER, state, SubtaskAssignment)
        subtask = work.subtask
        if await self._probe.is_cancelled(work.research_id, work.user_id):
            return {"stop": Stop(reason=StopReason.CANCELLED)}

        timeout = min(self._bounds.node_timeout_seconds, work.time_allowance_seconds)
        try:
            result = await self._call(
                lambda: self._nodes.researcher.research(work), limit_seconds=timeout
            )
            outcome = _expect(GraphNode.RESEARCHER, result.value, TaskOutcome)
        except _PROPAGATE:
            raise
        except Exception as exc:
            failure = self._failure(
                GraphNode.RESEARCHER, subtask.iteration, exc, task_key=subtask.key
            )
            return {"failed_tasks": [failure]}
        if outcome.task_key != subtask.key or outcome.iteration != subtask.iteration:
            raise NodeContractViolated(
                context={"node": _RESEARCHER, "assigned": subtask.key, "returned": outcome.task_key}
            )

        errors: list[NodeError] = []
        if len(outcome.sources) > work.source_allowance:
            errors.append(
                NodeError(
                    node=GraphNode.RESEARCHER,
                    iteration=subtask.iteration,
                    code="allowance_exceeded",
                    message=(
                        f"Reported {len(outcome.sources)} sources against an allowance of "
                        f"{work.source_allowance}; only the first {work.source_allowance} count."
                    ),
                    task_key=subtask.key,
                )
            )
            outcome = outcome.model_copy(
                update={"sources": outcome.sources[: work.source_allowance]}
            )
        if result.usage.search_queries > work.query_allowance:
            # Counted in full regardless: the searches were made, and the run's
            # ceiling is about what was spent, not what was allowed.
            errors.append(
                NodeError(
                    node=GraphNode.RESEARCHER,
                    iteration=subtask.iteration,
                    code="allowance_exceeded",
                    message=(
                        f"Used {result.usage.search_queries} searches against an allowance of "
                        f"{work.query_allowance}."
                    ),
                    task_key=subtask.key,
                )
            )

        update: Update = {
            "completed_tasks": [outcome],
            "sources": list(outcome.sources),
            "token_usage": result.usage.tokens,
            "estimated_cost": result.usage.cost,
            "search_queries": result.usage.search_queries,
        }
        if errors:
            update["errors"] = errors
        return update

    # --- processing ----------------------------------------------------------------

    async def evidence_extractor(self, state: ResearchState) -> Update:
        return await self._process(
            state,
            GraphNode.EVIDENCE_EXTRACTOR,
            "evidence",
            EvidenceItem,
            lambda: self._nodes.evidence_extractor.extract(state),
        )

    async def claim_normalizer(self, state: ResearchState) -> Update:
        return await self._process(
            state,
            GraphNode.CLAIM_NORMALIZER,
            "claims",
            ClaimItem,
            lambda: self._nodes.claim_normalizer.normalize(state),
        )

    async def verifier(self, state: ResearchState) -> Update:
        return await self._process(
            state,
            GraphNode.VERIFIER,
            "claims",
            ClaimItem,
            lambda: self._nodes.verifier.verify(state),
        )

    async def contradiction_checker(self, state: ResearchState) -> Update:
        return await self._process(
            state,
            GraphNode.CONTRADICTION_CHECKER,
            "contradictions",
            ContradictionItem,
            lambda: self._nodes.contradiction_checker.find_contradictions(state),
        )

    async def critic(self, state: ResearchState) -> Update:
        now = self._now()
        if await self._cancelled(state):
            return self._cancel(state, now)
        if state.get("stop") is not None:
            return self._accounted(state, now)
        iteration = state.get("iteration", 0)
        try:
            result = await self._call(lambda: self._nodes.critic.critique(state))
            critique = _expect(GraphNode.CRITIC, result.value, Critique)
        except _PROPAGATE:
            raise
        except Exception as exc:
            now = self._now()
            return {
                **self._accounted(state, now),
                **self._stop(state, StopReason.DISCOVERY_FAILED, self._used(state, now)),
                "errors": [self._failure(GraphNode.CRITIC, iteration, exc)],
            }
        if critique.iteration != iteration:
            raise NodeContractViolated(
                context={"node": _CRITIC, "expected": iteration, "returned": critique.iteration}
            )

        now = self._now()
        update: Update = {"critique": critique, **self._accounted(state, now, result.usage)}
        if not critique.sufficient:
            budget = state["budget"]
            used = self._used(state, now, result.usage)
            reason = governor.limit_reached(budget, used)
            if reason is None and not governor.may_plan_again(budget, used):
                reason = StopReason.ITERATIONS
            if reason is not None:
                update.update(self._stop(state, reason, used, critique=critique))
        return update

    # --- writing -------------------------------------------------------------------

    async def synthesizer(self, state: ResearchState) -> Update:
        now = self._now()
        if await self._cancelled(state):
            return self._cancel(state, now)
        try:
            result = await self._call(lambda: self._nodes.synthesizer.synthesize(state))
            draft = _expect(GraphNode.SYNTHESIZER, result.value, ReportDraft)
        except _PROPAGATE:
            raise
        except Exception as exc:
            raise SynthesisFailed(context={"research_id": str(state["research_id"])}) from exc

        # The revision and the caveat are facts about the run, so the graph
        # writes them: a synthesizer cannot drop the caveat a limit required.
        stop = state.get("stop")
        report = draft.model_copy(
            update={
                "revision": state.get("citation_repairs", 0),
                "coverage_caveat": None if stop is None else stop.caveat,
            }
        )
        return {
            "report": report,
            "citation_check": None,
            **self._accounted(state, self._now(), result.usage),
        }

    async def citation_validator(self, state: ResearchState) -> Update:
        now = self._now()
        if await self._cancelled(state):
            return self._cancel(state, now)
        report = state.get("report")
        if report is None:
            raise NodeContractViolated(context={"node": _VALIDATOR, "reason": "no draft"})
        try:
            result = await self._call(lambda: self._nodes.citation_validator.validate(state))
            check = _expect(GraphNode.CITATION_VALIDATOR, result.value, CitationCheck)
        except _PROPAGATE:
            raise
        except Exception as exc:
            raise CitationValidationFailed(
                context={"research_id": str(state["research_id"])}
            ) from exc
        if check.revision != report.revision:
            raise NodeContractViolated(
                context={
                    "node": _VALIDATOR,
                    "expected": report.revision,
                    "returned": check.revision,
                }
            )

        update: Update = {
            "citation_check": check,
            **self._accounted(state, self._now(), result.usage),
        }
        repairs = state.get("citation_repairs", 0)
        if not check.passed and repairs < governor.MAX_CITATION_REPAIRS:
            update["citation_repairs"] = repairs + 1
        return update

    # --- routing -------------------------------------------------------------------

    def after_planner(self, state: ResearchState) -> str | list[Send]:
        stop = stop_reason(state)
        if stop is StopReason.CANCELLED:
            return END
        plan = state.get("research_plan")
        if stop is not None or plan is None or not plan.subtasks:
            return _SYNTHESIZER

        budget = state["budget"]
        parameters = state["parameters"]
        used = consumption(state)
        dispatches = governor.allocate(
            budget, used, plan.subtasks, width=self._bounds.max_subtasks_per_iteration
        )
        time_left = budget.max_runtime_seconds - used.runtime_seconds
        if not dispatches or time_left <= 0:
            return _SYNTHESIZER
        return [
            Send(
                _RESEARCHER,
                SubtaskAssignment(
                    research_id=state["research_id"],
                    user_id=state["user_id"],
                    query=state["query"],
                    subtask=dispatch.subtask,
                    query_allowance=dispatch.query_allowance,
                    source_allowance=dispatch.source_allowance,
                    time_allowance_seconds=time_left,
                    domains=parameters.domains,
                    date_range_start=parameters.date_range_start,
                    date_range_end=parameters.date_range_end,
                ),
            )
            for dispatch in dispatches
        ]

    def after_critic(self, state: ResearchState) -> str:
        stop = stop_reason(state)
        if stop is StopReason.CANCELLED:
            return END
        critique = state.get("critique")
        if stop is not None or critique is None or critique.sufficient:
            return _SYNTHESIZER
        return _PLANNER

    def after_validation(self, state: ResearchState) -> str:
        if stop_reason(state) is StopReason.CANCELLED:
            return END
        check = state.get("citation_check")
        if check is None or check.passed:
            return END
        # A repair was granted for this draft exactly when the count of repairs
        # has moved past the draft's revision; otherwise the one repair is spent.
        if state.get("citation_repairs", 0) > check.revision:
            return _SYNTHESIZER
        return END

    # --- boundaries ----------------------------------------------------------------

    async def _process[V](
        self,
        state: ResearchState,
        node: GraphNode,
        field: str,
        item_type: type[V],
        work: Callable[[], Awaitable[NodeResult[tuple[V, ...]]]],
    ) -> Update:
        now = self._now()
        if await self._cancelled(state):
            return self._cancel(state, now)
        try:
            result = await self._call(work)
            items = _expect_items(node, result.value, item_type)
        except _PROPAGATE:
            raise
        except Exception as exc:
            return {
                **self._accounted(state, self._now()),
                "errors": [self._failure(node, state.get("iteration", 0), exc)],
            }
        return {field: list(items), **self._accounted(state, self._now(), result.usage)}

    async def _cancelled(self, state: ResearchState) -> bool:
        if stop_reason(state) is StopReason.CANCELLED:
            return True
        return await self._probe.is_cancelled(state["research_id"], state["user_id"])

    def _cancel(self, state: ResearchState, now: datetime) -> Update:
        return {**self._accounted(state, now), "stop": Stop(reason=StopReason.CANCELLED)}

    def _resumed_at(self) -> datetime:
        return get_runtime(GraphContext).context.resumed_at

    def _used(
        self, state: ResearchState, now: datetime, usage: NodeUsage | None = None
    ) -> governor.Consumption:
        used = consumption(state, now=now, resumed_at=self._resumed_at())
        if usage is None:
            return used
        return replace(
            used,
            cost=used.cost + usage.cost,
            search_queries=used.search_queries + usage.search_queries,
        )

    def _accounted(
        self, state: ResearchState, now: datetime, usage: NodeUsage | None = None
    ) -> Update:
        spent = usage or NodeUsage()
        clock = (state.get("clock") or RunClock()).advanced_to(now, resumed_at=self._resumed_at())
        return {
            "clock": clock,
            "token_usage": spent.tokens,
            "estimated_cost": spent.cost,
            "search_queries": spent.search_queries,
        }

    def _stop(
        self,
        state: ResearchState,
        reason: StopReason,
        used: governor.Consumption,
        *,
        critique: Critique | None = None,
    ) -> Update:
        # The caveat explains the first stop, and the reducer keeps the first
        # stop, so one is only worth building when there is no stop yet.
        caveat = None
        if state.get("stop") is None:
            caveat = governor.coverage_caveat(reason, state["budget"], used, critique=critique)
        return {"stop": Stop(reason=reason, caveat=caveat)}

    async def _call[T](
        self,
        work: Callable[[], Awaitable[NodeResult[T]]],
        *,
        limit_seconds: float | None = None,
    ) -> NodeResult[T]:
        limit = self._bounds.node_timeout_seconds if limit_seconds is None else limit_seconds
        async with asyncio.timeout(limit):
            result = await work()
        if not isinstance(result, NodeResult):
            raise NodeContractViolated(context={"returned": type(result).__name__})
        return result

    def _failure(
        self,
        node: GraphNode,
        iteration: int,
        exc: Exception,
        *,
        task_key: str | None = None,
    ) -> NodeError:
        """A failure as the trace records it.

        The message is written here, not taken from the exception: an agent's
        exception may quote the hostile page it was reading. An ``AppError``
        message is safe by that class's contract, so it is the one exception.
        """
        if isinstance(exc, TimeoutError):
            code, message = "node_timeout", "The step did not finish within its time limit."
        elif isinstance(exc, AppError) and _ERROR_CODE.fullmatch(exc.code):
            code, message = exc.code, exc.message[:1000]
        else:
            code, message = "node_failed", f"The step raised {type(exc).__name__}."
        logger.warning(
            "research node failed",
            extra={
                "node": node.value,
                "iteration": iteration,
                "task_key": task_key,
                "error_code": code,
                "error_type": type(exc).__name__,
            },
        )
        return NodeError(
            node=node, iteration=iteration, code=code, message=message, task_key=task_key
        )


def _expect[V](node: GraphNode, value: object, expected: type[V]) -> V:
    if not isinstance(value, expected):
        raise NodeContractViolated(
            context={
                "node": node.value,
                "expected": expected.__name__,
                "returned": type(value).__name__,
            }
        )
    return value


def _expect_items[V](node: GraphNode, value: object, item_type: type[V]) -> tuple[V, ...]:
    if not isinstance(value, tuple) or not all(isinstance(item, item_type) for item in value):
        raise NodeContractViolated(
            context={"node": node.value, "expected": f"tuple[{item_type.__name__}, ...]"}
        )
    return value
