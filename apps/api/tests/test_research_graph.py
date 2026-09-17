"""The research graph's shape and loop control, driven by scripted nodes.

Nothing here is an agent. The nodes are test doubles that return exactly what
each test scripts (``tests/support/graph.py``), so what is under test is the
graph itself: the order nodes run in, what runs in parallel, what a limit or a
cancellation stops, what a failure does, and that every loop ends.

The checkpointer is LangGraph's in-memory saver configured with the serializer
production uses, so a resumed run here is revived under the same rules as one
resumed from Postgres (``tests/test_graph_checkpoint.py`` covers Postgres).
"""

from __future__ import annotations

import langsmith.utils
import pytest

from app.agents.errors import NodeContractViolated, PlanningFailed, SynthesisFailed
from app.agents.nodes import NodeResult
from app.agents.schemas import ClaimItem, Plan, ReportDraft, StopReason
from app.core.enums import ClaimStatus, ResearchMode, TaskPriority
from tests.support.graph import RecordedRuns, Script, ScriptedNodes, brief, make_runner

EVERY_NODE_ONCE = {
    "planner": 1,
    "researcher": 2,
    "evidence_extractor": 1,
    "claim_normalizer": 1,
    "verifier": 1,
    "contradiction_checker": 1,
    "critic": 1,
    "synthesizer": 1,
    "citation_validator": 1,
}


# --- the happy path ---------------------------------------------------------------


async def test_a_deep_run_goes_from_question_to_validated_report():
    nodes = ScriptedNodes()
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief())

    assert dict(nodes.calls) == EVERY_NODE_ONCE
    assert isinstance(state["report"], ReportDraft)
    assert state["citation_check"] is not None and state["citation_check"].passed
    assert state["stop"] is None
    assert len(state["completed_tasks"]) == 2
    assert state["failed_tasks"] == []
    assert all(claim.status is ClaimStatus.VERIFIED for claim in state["claims"])
    # Ten calls at a hundred tokens each, two of them searches: summed, not guessed.
    assert state["token_usage"].total == 1000
    assert state["search_queries"] == 2


@pytest.mark.parametrize("concurrency", [1, 3])
async def test_researchers_run_in_parallel_up_to_the_concurrency_cap(concurrency):
    nodes = ScriptedNodes(Script(subtasks_per_round=6, research_delay_seconds=0.05))
    runner, _, _ = make_runner(nodes, concurrency=concurrency)

    await runner.run(brief())

    assert nodes.calls["researcher"] == 6
    assert nodes.peak_concurrent_research == concurrency


async def test_a_round_is_capped_and_dispatched_by_priority():
    priorities = (
        TaskPriority.LOW,
        TaskPriority.HIGH,
        TaskPriority.MEDIUM,
        TaskPriority.HIGH,
        TaskPriority.LOW,
    )
    nodes = ScriptedNodes(Script(subtasks_per_round=5, priorities=priorities))
    runner, _, _ = make_runner(nodes, width=3)

    await runner.run(brief())

    assert sorted(assignment.subtask.key for assignment in nodes.assignments) == [
        "round1-task1",
        "round1-task2",
        "round1-task3",
    ]
    # What was left of the budget, split three ways.
    assert {assignment.query_allowance for assignment in nodes.assignments} == {10}
    assert {assignment.source_allowance for assignment in nodes.assignments} == {16}


async def test_a_quick_run_skips_verification_contradictions_and_the_critic():
    """PRD 5.1. It keeps evidence and claims: a citation resolves through them."""
    nodes = ScriptedNodes()
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief(mode=ResearchMode.QUICK, max_iterations=1))

    assert nodes.calls["verifier"] == nodes.calls["contradiction_checker"] == 0
    assert nodes.calls["critic"] == 0
    assert nodes.calls["evidence_extractor"] == nodes.calls["claim_normalizer"] == 1
    assert state["report"] is not None
    assert all(claim.status is ClaimStatus.CANDIDATE for claim in state["claims"])


# --- every loop ends ----------------------------------------------------------------


async def test_a_critic_that_is_never_satisfied_stops_at_the_iteration_ceiling():
    """Four deep rounds is more supersteps than LangGraph's default recursion
    limit allows, so this also proves the derived limit is the one in force."""
    nodes = ScriptedNodes(Script(sufficient_on_round=None))
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief(max_iterations=4))

    assert nodes.calls["planner"] == nodes.calls["critic"] == 4
    assert nodes.calls["synthesizer"] == 1
    assert state["stop"].reason is StopReason.ITERATIONS
    assert "after 4 research rounds" in state["stop"].caveat
    assert "Pricing detail still missing after round 4" in state["stop"].caveat


async def test_an_empty_replan_ends_discovery_without_a_caveat():
    """Nothing left to research is an answer, not a limit."""
    nodes = ScriptedNodes(Script(sufficient_on_round=None, empty_plan_on_round=2))
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief())

    assert nodes.calls["planner"] == 2
    assert nodes.calls["researcher"] == 2
    assert nodes.calls["synthesizer"] == 1
    assert state["stop"] is None


async def test_reaching_the_source_ceiling_ends_discovery_but_not_the_run():
    nodes = ScriptedNodes(Script(sufficient_on_round=None))
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief(max_sources=2))

    assert nodes.calls["planner"] == 1
    assert nodes.calls["synthesizer"] == 1
    assert state["stop"].reason is StopReason.SOURCES
    assert "after collecting 2 sources" in state["stop"].caveat


async def test_reaching_the_search_ceiling_ends_discovery_but_not_the_run():
    nodes = ScriptedNodes(Script(sufficient_on_round=None))
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief(max_search_queries=2))

    assert nodes.calls["planner"] == 1
    assert state["stop"].reason is StopReason.SEARCH_QUERIES
    assert "after 2 searches" in state["stop"].caveat


async def test_reaching_the_cost_ceiling_ends_discovery_but_not_the_run():
    """Eight calls at a cent before the critic: the round that crossed the ceiling
    finishes, and the overshoot is what the caveat reports."""
    nodes = ScriptedNodes(Script(sufficient_on_round=None, cost_per_call=0.01))
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief(max_cost_usd=0.05))

    assert nodes.calls["planner"] == 1
    assert nodes.calls["synthesizer"] == 1
    assert state["stop"].reason is StopReason.COST
    assert "$0.08 of the $0.05" in state["stop"].caveat


async def test_an_uncosted_model_call_ends_discovery_after_its_round():
    """Unknown spend cannot be held to a ceiling, so no further round starts -
    but the round that met the unpriced call is allowed to finish."""
    nodes = ScriptedNodes(Script(sufficient_on_round=None, uncosted=True))
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief())

    assert nodes.calls["planner"] == 1
    assert nodes.calls["researcher"] == 2
    assert nodes.calls["synthesizer"] == 1
    assert state["stop"].reason is StopReason.COST_UNMEASURED
    assert "after round 1" in state["stop"].caveat


async def test_running_out_of_time_stops_a_round_before_it_is_dispatched():
    nodes = ScriptedNodes(Script(sufficient_on_round=None))
    runner, _, clock = make_runner(nodes)
    nodes.script.after["planner"] = lambda: clock.advance(301)

    state = await runner.run(brief(max_runtime_seconds=300))

    assert nodes.calls["planner"] == 1
    assert nodes.calls["researcher"] == 0
    assert nodes.calls["synthesizer"] == 1
    assert state["stop"].reason is StopReason.RUNTIME
    assert "after 301 seconds" in state["stop"].caveat


async def test_the_graph_writes_the_caveat_and_revision_into_the_report():
    """The scripted synthesizer drops both. A limit's caveat is a fact about the
    run, and a writer must not be able to omit it."""
    nodes = ScriptedNodes(Script(sufficient_on_round=None))
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief(max_sources=2))

    assert state["report"].coverage_caveat == state["stop"].caveat is not None
    assert state["report"].revision == 0


# --- cancellation -------------------------------------------------------------------


async def test_cancellation_stops_the_run_at_the_next_node():
    nodes = ScriptedNodes()
    runner, probe, _ = make_runner(nodes)
    nodes.script.after["planner"] = probe.cancel

    state = await runner.run(brief())

    assert nodes.calls["researcher"] == 0
    assert nodes.calls["synthesizer"] == nodes.calls["citation_validator"] == 0
    assert state["stop"].reason is StopReason.CANCELLED
    assert state["report"] is None
    assert state["stop"].caveat is None


# --- failures -------------------------------------------------------------------------


async def test_a_failing_researcher_is_recorded_and_the_round_goes_on():
    nodes = ScriptedNodes(Script(fail_on={"researcher": {1}}))
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief())

    assert len(state["completed_tasks"]) == 1
    [failure] = state["failed_tasks"]
    assert failure.code == "node_failed"
    assert failure.task_key is not None
    # Written by the graph, not copied from the exception, which might quote a page.
    assert failure.message == "The step raised RuntimeError."
    assert state["report"] is not None


async def test_a_researcher_that_never_returns_is_timed_out():
    nodes = ScriptedNodes(Script(hang={"researcher"}))
    runner, _, _ = make_runner(nodes, node_timeout=0.1)

    state = await runner.run(brief())

    assert [failure.code for failure in state["failed_tasks"]] == ["node_timeout", "node_timeout"]
    assert state["report"] is not None


async def test_a_researcher_over_its_source_allowance_is_held_to_it():
    nodes = ScriptedNodes(Script(sources_per_task=3))
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief(max_sources=4))

    assert len(state["sources"]) == 4
    assert [error.code for error in state["errors"]] == ["allowance_exceeded"] * 2


async def test_a_failed_first_plan_fails_the_run():
    """There is nothing to research and nothing to report."""
    nodes = ScriptedNodes(Script(fail_on={"planner": {1}}))
    runner, _, _ = make_runner(nodes)

    with pytest.raises(PlanningFailed):
        await runner.run(brief())
    assert nodes.calls["researcher"] == 0


async def test_a_failed_later_plan_ends_discovery_but_still_reports():
    nodes = ScriptedNodes(Script(sufficient_on_round=None, fail_on={"planner": {2}}))
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief())

    assert nodes.calls["planner"] == 2
    assert nodes.calls["synthesizer"] == 1
    assert state["stop"].reason is StopReason.DISCOVERY_FAILED
    assert [error.node.value for error in state["errors"]] == ["planner"]


async def test_a_failing_critic_ends_discovery_but_still_reports():
    nodes = ScriptedNodes(Script(fail_on={"critic": {1}}))
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief())

    assert nodes.calls["synthesizer"] == 1
    assert state["stop"].reason is StopReason.DISCOVERY_FAILED


async def test_a_failing_verifier_is_recorded_and_the_run_goes_on():
    nodes = ScriptedNodes(Script(fail_on={"verifier": {1}}))
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief())

    assert [error.node.value for error in state["errors"]] == ["verifier"]
    assert all(claim.status is ClaimStatus.CANDIDATE for claim in state["claims"])
    assert state["report"] is not None


async def test_a_failed_synthesis_fails_the_run():
    nodes = ScriptedNodes(Script(fail_on={"synthesizer": {1}}))
    runner, _, _ = make_runner(nodes)

    with pytest.raises(SynthesisFailed):
        await runner.run(brief())


# --- recording what a run produced (Phase 11) -----------------------------------


async def test_a_finished_run_hands_its_state_to_the_recorder():
    """A checkpoint is readable only by the graph, so a run nobody recorded has
    produced nothing anyone can open."""
    nodes = ScriptedNodes(Script())
    recorder = RecordedRuns()
    runner, _, _ = make_runner(nodes, recorder=recorder)

    state = await runner.run(brief())

    assert len(recorder.states) == 1
    assert recorder.states[0]["research_id"] == state["research_id"]
    assert recorder.claims == state["claims"], "what is recorded is what the run concluded"


async def test_a_run_that_failed_still_has_its_evidence_recorded():
    nodes = ScriptedNodes(Script(fail_on={"synthesizer": {1}}))
    recorder = RecordedRuns()
    runner, _, _ = make_runner(nodes, recorder=recorder)

    with pytest.raises(SynthesisFailed):
        await runner.run(brief())

    assert len(recorder.states) == 1, (
        "synthesis failing does not unfind the sources, and someone diagnosing "
        "the failure needs to see what the run had gathered"
    )
    assert recorder.claims, "the last checkpoint is what the graph had reached"


async def test_a_recorder_that_fails_after_a_failed_run_does_not_replace_the_failure():
    nodes = ScriptedNodes(Script(fail_on={"synthesizer": {1}}))
    runner, _, _ = make_runner(nodes, recorder=RecordedRuns(fails=True))

    with pytest.raises(SynthesisFailed):
        await runner.run(brief())


async def test_a_run_whose_results_cannot_be_stored_has_not_finished():
    nodes = ScriptedNodes(Script())
    runner, _, _ = make_runner(nodes, recorder=RecordedRuns(fails=True))

    with pytest.raises(RuntimeError, match="recording failed"):
        await runner.run(brief())


async def test_a_failed_run_resumes_from_its_last_checkpoint():
    """The promise the runner makes: calling again resumes at the node that
    failed, and every node that had finished is not called - or paid for - twice."""
    nodes = ScriptedNodes(Script(fail_on={"synthesizer": {1}}))
    runner, _, _ = make_runner(nodes)
    run = brief()

    with pytest.raises(SynthesisFailed):
        await runner.run(run)
    state = await runner.run(run)

    assert nodes.calls["planner"] == 1
    assert nodes.calls["researcher"] == 2
    assert nodes.calls["synthesizer"] == 2
    assert isinstance(state["research_plan"], Plan)
    assert all(isinstance(claim, ClaimItem) for claim in state["claims"])
    assert state["citation_check"] is not None and state["citation_check"].passed


async def test_a_node_that_breaks_its_contract_stops_the_run():
    class ProsePlanner(ScriptedNodes):
        async def plan(self, state) -> NodeResult[str]:  # type: ignore[override]
            await self._begin("planner")
            return NodeResult("First research the market, then pricing.")

    runner, _, _ = make_runner(ProsePlanner())

    with pytest.raises(NodeContractViolated):
        await runner.run(brief())


# --- citation repair ----------------------------------------------------------------


async def test_citations_are_repaired_at_most_once():
    nodes = ScriptedNodes(Script(citation_failures=5))
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief())

    assert nodes.calls["synthesizer"] == nodes.calls["citation_validator"] == 2
    assert state["citation_check"] is not None and not state["citation_check"].passed
    assert state["report"].revision == 1


async def test_a_repaired_draft_that_passes_ends_the_run():
    nodes = ScriptedNodes(Script(citation_failures=1))
    runner, _, _ = make_runner(nodes)

    state = await runner.run(brief())

    assert nodes.calls["synthesizer"] == nodes.calls["citation_validator"] == 2
    assert state["citation_check"] is not None and state["citation_check"].passed
    assert state["report"].revision == 1


# --- data leaving the system ----------------------------------------------------------


async def test_tracing_stays_off_even_when_the_environment_turns_it_on(monkeypatch):
    """LangSmith reads LANGSMITH_TRACING itself, past the typed settings layer.
    The runner turns it off, so prompts and retrieved documents cannot leave the
    system because a variable happened to be set."""
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    langsmith.utils.get_env_var.cache_clear()
    seen: list[object] = []
    try:
        # The environment really does turn tracing on, or this test proves nothing.
        assert langsmith.utils.tracing_is_enabled() is True
        nodes = ScriptedNodes()
        nodes.script.after["planner"] = lambda: seen.append(langsmith.utils.tracing_is_enabled())
        runner, _, _ = make_runner(nodes)
        await runner.run(brief())
    finally:
        monkeypatch.delenv("LANGSMITH_TRACING")
        # The value is cached: without this, every later test would see tracing on.
        langsmith.utils.get_env_var.cache_clear()

    assert seen == [False]
