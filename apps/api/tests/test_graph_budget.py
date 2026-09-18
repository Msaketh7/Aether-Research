"""Loop control as pure rules: which ceiling ends discovery, and what a report says.

Every expected value is arithmetic on a budget and a consumption, so each rule
is visible here without a graph. The graph tests then check that the graph
applies these rules and no others.
"""

from __future__ import annotations

import pytest

from app.agents.budget import (
    Consumption,
    allocate,
    coverage_caveat,
    limit_reached,
    may_plan_again,
    recursion_limit,
)
from app.agents.schemas import CostEstimate, Critique, MissingInfo, StopReason, Subtask
from app.core.enums import ResearchMode, TaskPriority
from app.models.budget import BudgetExhausted, ModelNotPriced
from tests.support.graph import Script, ScriptedNodes, brief, budget, make_runner

BUDGET = budget()


def used(**overrides: object) -> Consumption:
    values: dict[str, object] = {
        "iterations": 1,
        "sources": 0,
        "search_queries": 0,
        "runtime_seconds": 0.0,
        "cost": CostEstimate(),
    }
    values.update(overrides)
    return Consumption(**values)  # type: ignore[arg-type]


def subtask(key: str, priority: TaskPriority = TaskPriority.MEDIUM) -> Subtask:
    return Subtask(key=key, question=f"About {key}?", priority=priority, iteration=1)


# --- ceilings -------------------------------------------------------------------


def test_nothing_is_reached_while_everything_is_under_its_ceiling():
    assert limit_reached(BUDGET, used(sources=49, search_queries=29, runtime_seconds=299.9)) is None


@pytest.mark.parametrize(
    ("consumed", "reason"),
    [
        ({"cost": CostEstimate(usd=2.0)}, StopReason.COST),
        ({"runtime_seconds": 300.0}, StopReason.RUNTIME),
        ({"sources": 50}, StopReason.SOURCES),
        ({"search_queries": 30}, StopReason.SEARCH_QUERIES),
    ],
)
def test_each_ceiling_is_reached_at_its_own_value(consumed, reason):
    """At, not past: a ceiling of 50 sources allows 50, and the 50th ends discovery."""
    assert limit_reached(BUDGET, used(**consumed)) is reason


def test_cost_is_reported_first_when_several_ceilings_are_reached_at_once():
    """The reason a reader can act on first. Spend is the one with a bill attached."""
    everything = used(
        cost=CostEstimate(usd=5.0), runtime_seconds=900.0, sources=80, search_queries=60
    )
    assert limit_reached(BUDGET, everything) is StopReason.COST


def test_an_uncosted_call_ends_discovery_only_at_a_round_boundary():
    """Mid-round it is not yet a reason to stop: the round's research would be
    discarded, and its cost is already unknowable."""
    unmeasured = used(cost=CostEstimate(uncosted_calls=1))
    assert limit_reached(BUDGET, unmeasured) is StopReason.COST_UNMEASURED
    assert limit_reached(BUDGET, unmeasured, at_round_boundary=False) is None


def test_running_out_of_rounds_is_not_a_spend_ceiling():
    """It only matters when the critic wants more, which the graph decides."""
    exhausted = used(iterations=4)
    assert limit_reached(BUDGET, exhausted) is None
    assert may_plan_again(BUDGET, exhausted) is False
    assert may_plan_again(BUDGET, used(iterations=3)) is True


# --- allocation -----------------------------------------------------------------


def test_a_round_is_dispatched_by_priority_then_in_plan_order():
    plan = [
        subtask("a", TaskPriority.LOW),
        subtask("b", TaskPriority.HIGH),
        subtask("c", TaskPriority.MEDIUM),
        subtask("d", TaskPriority.HIGH),
    ]
    assert [item.subtask.key for item in allocate(BUDGET, used(), plan, width=8)] == [
        "b",
        "d",
        "c",
        "a",
    ]


def test_a_round_is_no_wider_than_its_width():
    plan = [subtask(f"t{index}") for index in range(10)]
    assert len(allocate(BUDGET, used(), plan, width=3)) == 3


def test_a_round_is_no_wider_than_what_is_left_to_search():
    """Five researchers with two searches left between them would be five
    researchers competing for two searches."""
    plan = [subtask(f"t{index}") for index in range(5)]
    round_ = allocate(BUDGET, used(search_queries=28), plan, width=8)
    assert len(round_) == 2
    assert {item.query_allowance for item in round_} == {1}


def test_what_is_left_is_divided_evenly_between_the_round():
    plan = [subtask(f"t{index}") for index in range(4)]
    round_ = allocate(BUDGET, used(search_queries=2, sources=2), plan, width=8)
    assert {(item.query_allowance, item.source_allowance) for item in round_} == {(7, 12)}


def test_nothing_is_dispatched_once_a_budget_is_spent():
    plan = [subtask("t")]
    assert allocate(BUDGET, used(sources=50), plan, width=8) == []
    assert allocate(BUDGET, used(search_queries=30), plan, width=8) == []
    assert allocate(BUDGET, used(), [], width=8) == []


def test_a_round_has_a_width_of_at_least_one():
    with pytest.raises(ValueError, match="at least one"):
        allocate(BUDGET, used(), [subtask("t")], width=0)


# --- the recursion backstop -----------------------------------------------------


@pytest.mark.parametrize("mode", list(ResearchMode))
@pytest.mark.parametrize("iterations", [1, 2, 4, 5])
def test_the_recursion_limit_covers_every_round_a_budget_allows(mode, iterations):
    """Seven steps a deep round, four a quick one, then synthesis, validation
    and the one repair. The graph tests run these shapes to completion."""
    per_round = 4 if mode is ResearchMode.QUICK else 7
    assert recursion_limit(mode, max_iterations=iterations) > per_round * iterations + 5


def test_a_four_round_deep_run_needs_more_than_langgraphs_default():
    """The reason the limit is derived: 25 would end this run with an exception."""
    assert recursion_limit(ResearchMode.DEEP, max_iterations=4) > 25


# --- caveats --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "consumed", "expected"),
    [
        (StopReason.COST, {"cost": CostEstimate(usd=2.13)}, "$2.13 of the $2.00"),
        (StopReason.COST_UNMEASURED, {"iterations": 2}, "after round 2"),
        (StopReason.RUNTIME, {"runtime_seconds": 301.4}, "after 301 seconds"),
        (StopReason.SOURCES, {"sources": 50}, "after collecting 50 sources"),
        (StopReason.SEARCH_QUERIES, {"search_queries": 30}, "after 30 searches"),
        (StopReason.DISCOVERY_FAILED, {}, "plans or judges further research failed"),
        (StopReason.ITERATIONS, {"iterations": 4}, "after 4 research rounds"),
    ],
)
def test_each_caveat_names_its_limit_and_what_was_measured(reason, consumed, expected):
    caveat = coverage_caveat(reason, BUDGET, used(**consumed))
    assert caveat is not None
    assert expected in caveat
    assert caveat.endswith("The report covers what had been gathered by then.")


def test_an_iterations_caveat_names_what_was_still_missing():
    critique = Critique(
        iteration=4,
        sufficient=False,
        missing=tuple(MissingInfo(description=f"gap {index}") for index in range(5)),
    )
    caveat = coverage_caveat(StopReason.ITERATIONS, BUDGET, used(iterations=4), critique=critique)
    assert caveat is not None
    assert "Still missing: gap 0; gap 1; gap 2; and 2 more." in caveat


def test_a_cancelled_run_carries_no_caveat():
    """It produces no report for a caveat to be read in."""
    assert coverage_caveat(StopReason.CANCELLED, BUDGET, used()) is None


# --- a refusal at the gateway ---------------------------------------------------
#
# The rules above are checked between nodes. A per-run ceiling is also enforced
# *before* each model call (Phase 16), and what the graph must do with that
# refusal is treat it as a limit rather than as a broken agent: FR-8 says a run
# that hits a limit finishes with a partial report, not that it fails.


async def test_a_refused_call_ends_discovery_and_still_writes_a_report():
    """The planner is refused before it spends anything.

    There is no plan, so there is nothing to research - and the run still has
    to produce the report that is its partial result.
    """
    nodes = ScriptedNodes(Script(raises={"planner": BudgetExhausted()}))
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief())

    assert state["stop"].reason is StopReason.COST
    assert nodes.calls["synthesizer"] == 1
    assert state["report"] is not None
    assert "cost" in (state["stop"].caveat or "").lower()


async def test_a_refusal_partway_through_a_round_stops_the_round():
    """The overshoot this bounds: a node that started under the ceiling and a
    sibling that would otherwise carry on spending past it."""
    nodes = ScriptedNodes(Script(raises={"evidence_extractor": BudgetExhausted()}))
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief())

    assert state["stop"].reason is StopReason.COST
    assert state["report"] is not None
    assert [error.code for error in state["errors"]] == ["budget_exhausted"]


async def test_an_unpriced_model_stops_the_run_the_same_way():
    """A ceiling that cannot be measured against is a ceiling that is not
    enforced, which the run says out loud rather than ignoring."""
    nodes = ScriptedNodes(Script(raises={"critic": ModelNotPriced()}))
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief())

    assert state["stop"].reason is StopReason.COST
    assert state["report"] is not None
