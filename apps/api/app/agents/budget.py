"""Loop control: the FR-8 ceilings, checked at every node boundary (TDD 4.4).

Pure functions over a run's budget and what it has consumed, so every rule is
tested without a graph, a model or a database - and the graph cannot quietly
disagree with them, because it decides nothing itself.

The rules, and why each is the way it is:

* **A reached limit ends discovery, not the run.** No planner or researcher
  runs again, and the run proceeds to synthesis with a caveat naming the limit
  and what was measured against it (FR-8). Stopping at a limit is a normal
  outcome with a partial report, not a failure.
* **Cancellation ends the run.** Nothing further runs, synthesis included.
* **An uncosted call ends discovery at the end of its round.** When a model's
  price is unknown the run's spend is unknown, and a cost ceiling that cannot
  be measured cannot be enforced - so no further round starts. The round in
  progress is allowed to finish: stopping it midway would discard research
  whose cost is already unknowable, and a run whose planner is unpriced would
  otherwise stop before researching anything. Phase 16 can refuse such a model
  before it is ever called.
* **A step that starts under a ceiling may finish over it.** Nodes are not
  interrupted mid-call: a half-finished model call is paid for and returns
  nothing. The overshoot is bounded by one step, and a round of researchers is
  bounded further by each one's share of what was left (``allocate``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.agents.schemas import CostEstimate, Critique, RunBudget, StopReason, Subtask
from app.core.enums import ResearchMode, TaskPriority

#: Validation may send a draft back to the synthesizer this many times (TDD 4.1).
MAX_CITATION_REPAIRS = 1

_PRIORITY_RANK = {TaskPriority.HIGH: 0, TaskPriority.MEDIUM: 1, TaskPriority.LOW: 2}

#: LangGraph supersteps in one research round. A round of parallel researchers
#: is one superstep however many run. Deep and conversational rounds: planner,
#: researchers, evidence, normalization, verification, contradictions, critic.
#: A quick round has no verification, contradiction check or critic.
_ROUND_STEPS = {ResearchMode.DEEP: 7, ResearchMode.CONVERSATIONAL: 7, ResearchMode.QUICK: 4}
#: After the last round: a planner that stops at a limit, then synthesis and
#: validation, and both again if the one repair is used.
_TAIL_STEPS = 1 + 2 * (1 + MAX_CITATION_REPAIRS)
#: Slack so that the recursion limit is never what ends a run. It is a backstop
#: for a routing defect; the ceilings above are the actual bound.
_RECURSION_MARGIN = 3

#: How many missing items an iterations caveat names before summarising.
_CAVEAT_MISSING_ITEMS = 3


@dataclass(frozen=True, slots=True)
class Consumption:
    """What a run has used so far, as the governor reads it from state."""

    iterations: int
    sources: int
    search_queries: int
    runtime_seconds: float
    cost: CostEstimate


@dataclass(frozen=True, slots=True)
class Dispatch:
    """One subtask sent to a researcher, with its share of the budget."""

    subtask: Subtask
    query_allowance: int
    source_allowance: int


def limit_reached(
    budget: RunBudget, used: Consumption, *, at_round_boundary: bool = True
) -> StopReason | None:
    """The ceiling that ends discovery now, if any.

    ``at_round_boundary`` is false for a check made partway through a round,
    where an uncosted call is not yet a reason to stop (see the module notes).
    Iterations are not checked here: running out of rounds is only a limit
    when the critic still wants more, and ``may_plan_again`` answers that.
    """
    if used.cost.usd >= budget.max_cost_usd:
        return StopReason.COST
    if at_round_boundary and not used.cost.measured:
        return StopReason.COST_UNMEASURED
    if used.runtime_seconds >= budget.max_runtime_seconds:
        return StopReason.RUNTIME
    if used.sources >= budget.max_sources:
        return StopReason.SOURCES
    if used.search_queries >= budget.max_search_queries:
        return StopReason.SEARCH_QUERIES
    return None


def may_plan_again(budget: RunBudget, used: Consumption) -> bool:
    return used.iterations < budget.max_iterations


def allocate(
    budget: RunBudget,
    used: Consumption,
    subtasks: Sequence[Subtask],
    *,
    width: int,
) -> list[Dispatch]:
    """The subtasks to research this round, highest priority first, with allowances.

    A round is as wide as the plan, capped by ``width`` and by what is left of
    the query and source budgets: eight researchers with three searches left
    between them would be eight researchers competing for three searches. What
    is left is divided evenly and handed out, so no researcher can spend a
    sibling's share - they run in parallel and cannot see each other's progress.
    """
    if width < 1:
        raise ValueError("A research round must be at least one subtask wide.")
    queries_left = budget.max_search_queries - used.search_queries
    sources_left = budget.max_sources - used.sources
    if not subtasks or queries_left < 1 or sources_left < 1:
        return []

    ordered = sorted(
        enumerate(subtasks), key=lambda item: (_PRIORITY_RANK[item[1].priority], item[0])
    )
    count = min(len(ordered), width, queries_left, sources_left)
    return [
        Dispatch(
            subtask=subtask,
            query_allowance=queries_left // count,
            source_allowance=sources_left // count,
        )
        for _, subtask in ordered[:count]
    ]


def recursion_limit(mode: ResearchMode, *, max_iterations: int) -> int:
    """LangGraph's superstep ceiling for a run of this shape.

    LangGraph's default is 25, which a deep run at four iterations exceeds by
    design - and the failure it produces is an exception, not a report. Derived
    from the topology instead, with a margin, so the budget ends a run and this
    only ever catches a routing defect.
    """
    if max_iterations < 1:
        raise ValueError("A run has at least one iteration.")
    return _ROUND_STEPS[mode] * max_iterations + _TAIL_STEPS + _RECURSION_MARGIN


def coverage_caveat(
    reason: StopReason,
    budget: RunBudget,
    used: Consumption,
    *,
    critique: Critique | None = None,
) -> str | None:
    """The sentence a report carries when discovery ended early (FR-8).

    Built from measured values only. ``None`` for a cancelled run, which
    produces no report to carry it.
    """
    covers = "The report covers what had been gathered by then."
    match reason:
        case StopReason.CANCELLED:
            return None
        case StopReason.COST:
            return (
                f"Research stopped when its estimated cost reached ${used.cost.usd:.2f} of the "
                f"${budget.max_cost_usd:.2f} this run was allowed. {covers}"
            )
        case StopReason.COST_UNMEASURED:
            return (
                f"Research stopped after round {used.iterations} because a model call could not "
                f"be costed, so this run's spending limit could not be enforced. {covers}"
            )
        case StopReason.RUNTIME:
            return (
                f"Research stopped after {used.runtime_seconds:.0f} seconds of work, the most "
                f"this run was allowed ({budget.max_runtime_seconds}). {covers}"
            )
        case StopReason.SOURCES:
            return (
                f"Research stopped after collecting {used.sources} sources, the most this run "
                f"was allowed. {covers}"
            )
        case StopReason.SEARCH_QUERIES:
            return (
                f"Research stopped after {used.search_queries} searches, the most this run was "
                f"allowed. {covers}"
            )
        case StopReason.DISCOVERY_FAILED:
            return (
                "Research stopped early because a step that plans or judges further research "
                f"failed. {covers}"
            )
        case StopReason.ITERATIONS:
            missing = ""
            if critique is not None and critique.missing:
                named = [item.description for item in critique.missing[:_CAVEAT_MISSING_ITEMS]]
                extra = len(critique.missing) - len(named)
                missing = " Still missing: " + "; ".join(named)
                missing += f"; and {extra} more." if extra else "."
            return (
                f"Coverage was still judged incomplete after {used.iterations} research rounds, "
                f"the most this run was allowed.{missing} {covers}"
            )
