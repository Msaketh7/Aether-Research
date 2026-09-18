"""Scenarios 3 and 4: the model provider throttles, stalls, or stays down.

A gateway exists so that a caller reacts to *what went wrong* rather than to
whichever SDK exception surfaced, and the two loops inside it do different jobs:
the inner one waits and asks the same model again, the outer one gives up on
that model and asks the next. Both of those are invisible from a unit test of an
agent, and both change what a user gets.

So these drive whole runs and assert on the run: does it finish, what does it
cost, and can a reader tell from the ledger which model actually answered.
"""

from __future__ import annotations

from sqlalchemy import select

from app.agents.outputs import CritiqueOutput, PlanOutput, SearchQueries
from app.core.enums import AgentName, LlmCallStatus, RunStatus
from app.db.models.report import ReportRow
from app.models.errors import ProviderRateLimited, ProviderTimeout
from tests.scenarios import story
from tests.scenarios.catalogue import Scenario, covers
from tests.scenarios.world import queue_run, run_until, scripted_dns
from tests.support.worker import read_row, settled

TERMINAL = (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)


def calls_by(world, role: AgentName):
    return [call for call in world.telemetry.calls.calls if call.role is role]


# --- throttling ---------------------------------------------------------------------


@covers(Scenario.LLM_RATE_LIMIT)
async def test_a_throttled_call_is_waited_out_and_the_run_finishes(make_world, database):
    """One 429, honoured and retried on the same model.

    The point of the inner loop: a transient throttle must cost a pause, not a
    subtask. ``retry_after_seconds`` is the provider's own advice, and the
    gateway is required to prefer it to its own curve.
    """
    brain = story.ordinary_run()
    brain.fails(SearchQueries, ProviderRateLimited("slow down", retry_after_seconds=0.01))
    world = make_world(web=story.web(), brain=brain)

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, *TERMINAL))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.COMPLETED, row.error
    assert row.source_count == 1

    attempts = calls_by(world, AgentName.RESEARCHER)
    throttled = [call for call in attempts if call.status is LlmCallStatus.ERROR]
    answered = [call for call in attempts if call.status is LlmCallStatus.OK]
    assert [call.error_code for call in throttled] == ["provider_rate_limited"]
    assert answered, "the retry answered"
    assert throttled[0].model == answered[0].model, "retried on the same model, not failed over"
    assert throttled[0].attempt == 1 and answered[0].attempt == 2
    assert throttled[0].cost_usd == 0.0 or throttled[0].prompt_tokens == 0, (
        "a call that returned nothing spent no tokens, and must not be priced as if it had"
    )


@covers(Scenario.LLM_RATE_LIMIT)
async def test_a_model_that_keeps_throttling_is_abandoned_for_the_next_one(make_world, database):
    """The outer loop: attempts exhausted on one model, so try another.

    Asserted through the ledger rather than through the answer, because the
    answer is identical either way - which is exactly why a silent loss of
    failover would never be noticed without this.
    """
    brain = story.ordinary_run()
    limit = ProviderRateLimited("slow down", retry_after_seconds=0.01)
    brain.fails(SearchQueries, *([limit] * 3))
    world = make_world(web=story.web(), brain=brain)

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, *TERMINAL))

    assert RunStatus((await read_row(database, run.id)).status) is RunStatus.COMPLETED

    attempts = calls_by(world, AgentName.RESEARCHER)
    exhausted = [call for call in attempts if call.status is LlmCallStatus.ERROR]
    recovered = next(call for call in attempts if call.status is LlmCallStatus.FALLBACK)
    assert len(exhausted) == world.settings.llm_max_attempts, "every attempt on the first model"
    assert len({call.model for call in exhausted}) == 1
    assert recovered.model != exhausted[0].model, "the answer came from a different model"
    # ``fell_back_from`` names the abandoned model by its *registry key*, while
    # ``model`` is the vendor's model id. Two adjacent columns in two different
    # vocabularies: worth knowing before reading `llm_calls`, and asserted here
    # so a change to either is a deliberate one.
    assert recovered.fell_back_from == "medium"
    assert exhausted[0].model == "scripted-medium"


# --- stalling -----------------------------------------------------------------------


@covers(Scenario.LLM_TIMEOUT)
async def test_a_critic_that_never_answers_ends_discovery_instead_of_the_run(make_world, database):
    """The whole fallback chain stalls on one node. The run still reports.

    The blast radius of a failed agent is the graph's decision, not the agent's,
    and the critic's is the smallest: without a critique there is no case for
    another round, so discovery ends and the report is written from what the run
    already gathered. Failing the run here would throw away a finished corpus
    because the step that decides whether to gather *more* did not answer.
    """
    brain = story.ordinary_run()
    stalled = ProviderTimeout("the model did not respond in time")
    brain.fails(CritiqueOutput, *([stalled] * 12))
    world = make_world(web=story.web(), brain=brain)

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, *TERMINAL))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.COMPLETED, row.error
    assert row.claim_count == 1

    async with database.session() as session:
        assert await session.scalar(select(ReportRow).where(ReportRow.run_id == run.id)) is not None

    stalls = [
        call for call in calls_by(world, AgentName.CRITIC) if call.status is LlmCallStatus.ERROR
    ]
    assert {call.error_code for call in stalls} == {"provider_timeout"}
    assert len({call.model for call in stalls}) > 1, (
        "a timeout is a failover reason as well as a retry reason, so the whole chain "
        "was tried before the node was given up on"
    )


@covers(Scenario.LLM_TIMEOUT)
async def test_a_planner_that_never_answers_fails_the_run_without_a_report(make_world, database):
    """The one node whose failure there is no carrying on from.

    A run with no plan has nothing to research, so ``PlanningFailed`` is raised
    rather than recorded. It is retryable - a stalled provider usually recovers -
    so the worker spends the run's attempts first, and only then fails it.
    """
    brain = story.ordinary_run()
    stalled = ProviderTimeout("the model did not respond in time")
    brain.fails(PlanOutput, *([stalled] * 24))
    world = make_world(web=story.web(), brain=brain)

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, *TERMINAL))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.FAILED
    assert row.error["code"] == "planning_failed"
    assert row.attempts == world.settings.worker_max_attempts, "retried before it was given up on"
    assert world.web.searches == [], "nothing was researched, because nothing was planned"

    async with database.session() as session:
        assert await session.scalar(select(ReportRow).where(ReportRow.run_id == run.id)) is None
