"""The two agents that steer the loop: what to research, and whether to stop.

Both spend the run's budget with their answer - a plan dispatches researchers, an
insufficient critique buys another round - so what is tested here is mostly what
the *agent* decides rather than what the model says: the keys, the iteration, the
channel, and the correction applied to a verdict the graph could not act on.
"""

from __future__ import annotations

import pytest

from app.agents.base import AgentContext
from app.agents.critic import CriticAgent
from app.agents.outputs import CritiqueOutput, MissingItem, PlanOutput, ProposedSubtask
from app.agents.planner import PlannerAgent
from app.agents.schemas import Critique, MissingInfo, ResearchChannel
from app.core.enums import AgentName, LlmCallStatus, ResearchMode, TaskPriority
from app.models.errors import ProviderUnavailable
from tests.support import agents as fake


def proposed(
    question: str = "What does each provider charge?", **overrides: object
) -> ProposedSubtask:
    values = {
        "question": question,
        "priority": TaskPriority.HIGH,
        "channel": ResearchChannel.WEB,
    }
    values.update(overrides)
    return ProposedSubtask.model_validate(values)


def plan_output(*subtasks: ProposedSubtask, goal: str = "Compare the providers") -> PlanOutput:
    return PlanOutput(research_goal=goal, subtasks=subtasks)


# --- the planner ----------------------------------------------------------------


async def test_the_planner_assigns_keys_rather_than_taking_them_from_the_model():
    """Two rounds must not collide on a key, and only the agent knows the round."""
    round_one = plan_output(proposed(), proposed("And capacity?"))
    round_two = plan_output(proposed("What changed since?"), proposed("And in Europe?"))
    gateway, _, _ = fake.gateway(round_one, round_two)
    agent = PlannerAgent(gateway, dispatch_width=4)

    first = await agent.plan(fake.state())
    second = await agent.plan(fake.state(iteration=1))

    assert [task.key for task in first.value.subtasks] == ["i1-1", "i1-2"]
    assert [task.key for task in second.value.subtasks] == ["i2-1", "i2-2"]


async def test_the_plan_belongs_to_the_round_the_graph_asked_for():
    """The graph raises a contract violation on a mismatch, so it is never the
    model's number."""
    gateway, _, _ = fake.gateway(plan_output(proposed()))
    result = await PlannerAgent(gateway, dispatch_width=4).plan(fake.state(iteration=2))

    assert result.value.iteration == 3
    assert all(task.iteration == 3 for task in result.value.subtasks)


async def test_a_document_subtask_becomes_a_web_subtask_when_nothing_is_attached():
    """A stored plan that says `documents` for a run with no documents would read,
    months later, as a subtask that was never run."""
    gateway, _, _ = fake.gateway(plan_output(proposed(channel=ResearchChannel.DOCUMENTS)))
    result = await PlannerAgent(gateway, dispatch_width=4).plan(fake.state())

    assert result.value.subtasks[0].channel is ResearchChannel.WEB


async def test_a_document_subtask_survives_when_the_run_has_documents():
    gateway, _, _ = fake.gateway(plan_output(proposed(channel=ResearchChannel.DOCUMENTS)))
    state = fake.state(parameters=fake.parameters(has_attached_documents=True))

    result = await PlannerAgent(gateway, dispatch_width=4).plan(state)

    assert result.value.subtasks[0].channel is ResearchChannel.DOCUMENTS


async def test_more_subtasks_than_allowed_are_truncated_rather_than_refused():
    gateway, _, _ = fake.gateway(plan_output(*[proposed(f"Question {n}?") for n in range(9)]))
    result = await PlannerAgent(gateway, max_subtasks=5, dispatch_width=3).plan(fake.state())

    assert len(result.value.subtasks) == 5


async def test_an_empty_plan_is_a_legitimate_answer():
    """A re-plan that finds nothing worth researching ends discovery cleanly."""
    gateway, _, _ = fake.gateway(plan_output())
    result = await PlannerAgent(gateway, dispatch_width=4).plan(fake.state(iteration=2))

    assert result.value.subtasks == ()


async def test_the_first_round_is_told_there_is_no_prior_work():
    gateway, model, _ = fake.gateway(plan_output(proposed()))
    await PlannerAgent(gateway, dispatch_width=4).plan(fake.state())

    assert "this is the first round" in model.user_prompts()[0]


async def test_a_later_round_is_given_the_critic_gaps_and_what_failed():
    gateway, model, _ = fake.gateway(plan_output(proposed()))
    state = fake.state(
        iteration=1,
        subtasks=[fake.subtask("i1-1")],
        claims=[fake.claim("c1")],
        critique=Critique(
            iteration=1,
            sufficient=False,
            missing=(MissingInfo(description="List prices per GPU-hour", task_key="i1-1"),),
        ),
    )

    await PlannerAgent(gateway, dispatch_width=4).plan(state)

    prompt = model.user_prompts()[0]
    assert "List prices per GPU-hour" in prompt
    assert "Subtasks already researched" in prompt


async def test_the_planner_reports_what_the_registry_says_the_call_cost():
    """Cost is measured through the gateway, never assembled by the agent."""
    gateway, model, recorder = fake.gateway(plan_output(proposed()))
    result = await PlannerAgent(gateway, dispatch_width=4).plan(fake.state())

    # 120 prompt tokens at $1/Mtok plus 60 completion tokens at $2/Mtok.
    assert result.usage.tokens.total == 180
    assert result.usage.cost.usd == pytest.approx(0.00024)
    assert result.usage.cost.measured
    assert [call.role for call in recorder.calls] == [AgentName.PLANNER]
    assert recorder.calls[0].prompt_version == "planner/v1"
    assert model.calls == 1


async def test_an_unpriced_model_is_reported_as_uncosted_rather_than_free():
    """The governor stops discovery at the end of a round whose cost is unknown.
    That only works if the agent says so instead of reporting zero."""
    gateway, _, _ = fake.gateway(plan_output(proposed()), priced=False)
    result = await PlannerAgent(gateway, dispatch_width=4).plan(fake.state())

    assert result.usage.cost.usd == 0.0
    assert result.usage.cost.uncosted_calls == 1
    assert not result.usage.cost.measured


async def test_a_model_failure_propagates_for_the_graph_to_classify():
    """The agent does not decide what a failure costs the run: the first
    planning round ends it, a later one only ends discovery."""
    gateway, _, recorder = fake.gateway(failures=[ProviderUnavailable("down")])
    with pytest.raises(ProviderUnavailable):
        await PlannerAgent(gateway, dispatch_width=4).plan(fake.state())

    assert [call.status for call in recorder.calls] == [LlmCallStatus.ERROR]


async def test_the_planner_routes_to_its_role_and_the_runs_mode():
    gateway, model, recorder = fake.gateway(plan_output(proposed()))
    state = fake.state(parameters=fake.parameters(mode=ResearchMode.QUICK))

    await PlannerAgent(gateway, dispatch_width=4).plan(state)

    assert recorder.calls[0].mode is ResearchMode.QUICK
    # Quick steps a role down a tier: the planner's base is small, and small is
    # the bottom of the ladder, so it stays there.
    assert model.prompts[0].model == "scripted-small"


# --- the critic ---------------------------------------------------------------------


async def test_the_critic_judges_the_round_the_graph_just_finished():
    gateway, _, _ = fake.gateway(CritiqueOutput(sufficient=True, rationale="covered"))
    result = await CriticAgent(gateway).critique(fake.state(iteration=2, claims=[fake.claim()]))

    assert result.value.iteration == 2
    assert result.value.sufficient


async def test_an_insufficient_verdict_maps_its_gaps_onto_real_subtasks():
    gateway, _, _ = fake.gateway(
        CritiqueOutput(
            sufficient=False,
            missing=(
                MissingItem(description="Prices for provider B", subtask=1),
                MissingItem(description="Anything about capacity", subtask=None),
            ),
        )
    )
    state = fake.state(iteration=1, subtasks=[fake.subtask("i1-1")], claims=[fake.claim()])

    result = await CriticAgent(gateway).critique(state)

    assert not result.value.sufficient
    assert [gap.task_key for gap in result.value.missing] == ["i1-1", None]


async def test_a_gap_pinned_to_a_subtask_that_does_not_exist_loses_the_pin():
    """The gap survives - it is still missing information - but it stops
    claiming to belong to a subtask nobody planned."""
    gateway, _, _ = fake.gateway(
        CritiqueOutput(
            sufficient=False,
            missing=(MissingItem(description="Prices for provider B", subtask=9),),
        )
    )
    state = fake.state(iteration=1, subtasks=[fake.subtask("i1-1")], claims=[fake.claim()])

    result = await CriticAgent(gateway).critique(state)

    assert result.value.missing[0].task_key is None
    assert result.value.missing[0].description == "Prices for provider B"


async def test_insufficient_with_nothing_missing_is_corrected_to_sufficient():
    """``Critique`` refuses the combination, and the graph would loop on it: the
    next planning round would have nothing to research and come straight back."""
    gateway, _, _ = fake.gateway(CritiqueOutput(sufficient=False, missing=()))
    result = await CriticAgent(gateway).critique(fake.state(iteration=1, claims=[fake.claim()]))

    assert result.value.sufficient
    assert result.value.missing == ()


async def test_the_critic_is_shown_what_failed_as_well_as_what_was_found():
    """A subtask that returned nothing is the strongest reason to run another
    round, and it is invisible in a list of claims."""
    from app.agents.schemas import GraphNode, NodeError

    gateway, model, _ = fake.gateway(CritiqueOutput(sufficient=True))
    state = fake.state(
        iteration=1,
        claims=[fake.claim()],
        failed_tasks=[
            NodeError(
                node=GraphNode.RESEARCHER,
                iteration=1,
                code="node_timeout",
                message="The step did not finish within its time limit.",
                task_key="i1-2",
            )
        ],
    )

    await CriticAgent(gateway).critique(state)

    assert "1 subtask attempt(s) returned nothing: i1-2" in model.user_prompts()[0]


async def test_contradictions_are_shown_to_the_critic_as_findings_not_gaps():
    gateway, model, _ = fake.gateway(CritiqueOutput(sufficient=True))
    state = fake.state(
        iteration=1,
        claims=[
            fake.claim("claim-1", key="price | h100 | 2026"),
            fake.claim("claim-2", key="price | h100 | 2026"),
        ],
        contradictions=[fake.contradiction()],
    )

    await CriticAgent(gateway).critique(state)

    assert "findings to report, not gaps to research away" in model.user_prompts()[0]


async def test_the_critic_context_carries_the_run_and_round_for_the_ledger():
    gateway, _, recorder = fake.gateway(CritiqueOutput(sufficient=True))
    await CriticAgent(gateway).critique(fake.state(iteration=3, claims=[fake.claim()]))

    assert recorder.calls[0].run_id == fake.RESEARCH_ID
    assert recorder.calls[0].role is AgentName.CRITIC


def test_the_agent_context_is_what_joins_a_log_line_back_to_a_run():
    context = AgentContext(
        research_id=fake.RESEARCH_ID,
        user_id=fake.USER_ID,
        mode=ResearchMode.DEEP,
        iteration=2,
        task_key="i2-1",
    )
    assert context.task_key == "i2-1"
