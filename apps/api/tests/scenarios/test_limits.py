"""Scenarios 10 and 11: a run that reaches a ceiling.

FR-8's ceilings are not failures, and that distinction is the whole design. A
run that spends its budget or uses its last round has *finished* - with less
coverage than it wanted, and with a report that says so. A system that failed
here would throw away everything it had already gathered at the exact moment
that work became most expensive to repeat.

Both are asserted end to end because both are decided in two places at once: the
guard refuses the call, and the graph decides what a refusal costs the run. Each
half looks correct on its own while the pair loses the report.
"""

from __future__ import annotations

from sqlalchemy import select

from app.agents.outputs import PlanOutput
from app.core.enums import LlmCallStatus, RunStatus
from app.db.models.evidence import ClaimRow, EvidenceRow
from app.db.models.report import ReportRow
from tests.scenarios import story
from tests.scenarios.catalogue import Scenario, covers
from tests.scenarios.world import queue_run, run_until, scripted_dns
from tests.support.worker import read_row, settled

TERMINAL = (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)

#: What one scripted call costs: 120 prompt tokens at $1/M and 60 completion
#: tokens at $2/M, as the test registry declares them. Named because the budget
#: scenario below is arithmetic on it, and a magic 0.0012 would not survive
#: anyone changing the scripted token counts.
CALL_USD = 120 * 1.0 / 1_000_000 + 60 * 2.0 / 1_000_000


@covers(Scenario.BUDGET_EXCEEDED)
async def test_a_run_that_spends_its_budget_still_writes_the_report_it_paid_for(
    make_world, database
):
    """The ceiling is reached mid-run, and the run ends as a completed one.

    Five calls of headroom: enough to plan, search, choose, quote and claim, and
    not enough to verify. So the refusal lands after there is something worth
    reporting, which is the case that matters - a budget spent before any
    evidence exists is just an expensive way of finding nothing.
    """
    world = make_world(
        web=story.web(),
        brain=story.ordinary_run(),
        max_estimated_cost_usd=round(5 * CALL_USD, 6),
    )

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, *TERMINAL))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.COMPLETED, row.error
    assert row.claim_count == 1, "what it had already gathered is kept"
    assert "cost" in (row.coverage_caveat or "").lower(), (
        "a partial result has to say why it is partial, or a reader cannot tell it "
        "from a thorough answer"
    )

    refusals = [
        call
        for call in world.telemetry.calls.calls
        if call.status is LlmCallStatus.ERROR and call.error_code == "budget_exhausted"
    ]
    assert refusals, "the ceiling is enforced before the call, and the refusal is a row"

    async with database.session() as session:
        report = await session.scalar(select(ReportRow).where(ReportRow.run_id == run.id))
    assert report is not None, "FR-8: a run that reaches a limit returns a partial result"
    assert report.coverage_caveat == row.coverage_caveat


@covers(Scenario.BUDGET_EXCEEDED)
async def test_the_synthesizer_is_paid_for_out_of_a_budget_that_is_already_spent(
    make_world, database
):
    """The one exemption, and the reason for it.

    Every other role is refused once the ceiling is reached. The step that writes
    the report is not, because the partial result FR-8 promises *is* the report
    and it costs a call. Asserted at the end of a real run rather than against
    the guard alone, since a graph that routed elsewhere on a refusal would make
    the exemption unreachable without changing it.

    The same five calls of headroom as above, and for the same reason: the
    exemption only means anything once the run has a claim to report. A budget
    exhausted before any evidence exists leaves the synthesizer allowed to spend
    and nothing to spend it on, and the run fails for having nothing to cite.
    """
    world = make_world(
        web=story.web(),
        brain=story.ordinary_run(),
        max_estimated_cost_usd=round(5 * CALL_USD, 6),
    )

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, *TERMINAL))

    assert RunStatus((await read_row(database, run.id)).status) is RunStatus.COMPLETED
    assert world.brain.calls_for(story.ReportOutput) == 1, "the report was written"

    row = await read_row(database, run.id)
    assert float(row.total_cost_usd) > round(5 * CALL_USD, 6), (
        "the run finished over its ceiling, because writing the report is allowed to "
        "take it there - which is a deliberate overshoot, not an unenforced limit"
    )


@covers(Scenario.MAX_ITERATIONS_REACHED)
async def test_a_critic_that_is_never_satisfied_stops_at_the_last_round(make_world, database):
    """Two rounds, a critic that always wants more, and a report at the end.

    The caveat is built by the graph from measured values, not written by the
    synthesizer: what was still missing is what the critic said, so a run that
    stopped for want of rounds can tell a reader what it did not get to.
    """
    brain = story.ordinary_run(sufficient=False)
    # The second round asks a different question, so its subtask is a new one
    # rather than the first one planned again. Narrowed by what only a second
    # planning prompt contains: the critic's own words about the gap.
    brain.on(
        PlanOutput,
        story.plan("What do other providers charge for H100 capacity?"),
        when="Pricing for the other providers",
    )
    world = make_world(web=story.web(), brain=brain, max_research_iterations=2)

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, *TERMINAL))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.COMPLETED, row.error
    assert row.iteration_count == 2, "it used its rounds and stopped, rather than looping"
    assert world.brain.calls_for(story.CritiqueOutput) == 2

    caveat = row.coverage_caveat or ""
    assert "Pricing for the other providers" in caveat, (
        "the caveat names what the critic said was missing, so the limit is legible "
        "as a gap in the answer rather than as a number"
    )

    async with database.session() as session:
        report = await session.scalar(select(ReportRow).where(ReportRow.run_id == run.id))
    assert report is not None and report.coverage_caveat == caveat


@covers(Scenario.MAX_ITERATIONS_REACHED)
async def test_a_second_round_corroborates_rather_than_duplicating_the_claim(make_world, database):
    """Why the loop is worth running at all, and why claim identity is derived.

    The same assertion found in a second source is one claim with two spans
    behind it, not two claims with half the support each. Round two reads the
    other page and the claim's evidence accumulates onto the id round one
    created.
    """
    brain = story.ordinary_run(sufficient=False)
    brain.on(
        PlanOutput,
        story.plan("Is provider A's H100 price corroborated anywhere else?"),
        when="Pricing for the other providers",
    )
    # Round one reads the price list; round two is offered only the review.
    brain.on(story.SourceSelection, story.choosing(story.PRICE_LIST.url))
    brain.on(
        story.SourceSelection,
        story.choosing(story.MARKET_REVIEW.url),
        when="corroborated anywhere else",
    )
    brain.on(story.EvidenceOutput, story.quoting(*story.QUOTES.values()))
    world = make_world(web=story.web(), brain=brain, max_research_iterations=2)

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, *TERMINAL))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.COMPLETED, row.error
    assert row.source_count == 2, "a second page, found in the second round"
    assert row.claim_count == 1, "one assertion, corroborated - not two half-supported ones"

    async with database.session() as session:
        claim = (
            await session.execute(select(ClaimRow).where(ClaimRow.run_id == run.id))
        ).scalar_one()
        spans = (
            (await session.execute(select(EvidenceRow).where(EvidenceRow.claim_id == claim.id)))
            .scalars()
            .all()
        )
    assert len({span.source_id for span in spans}) == 2, "two sources behind the one claim"
