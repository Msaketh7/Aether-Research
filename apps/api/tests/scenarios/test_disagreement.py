"""Scenario 7: two sources that do not agree.

FR-7, and the requirement that is easiest to satisfy on paper and hardest to
satisfy in a running system. A contradiction needs two claims that assert the
same thing with different values, and every step between a hostile page and that
pair has to preserve the difference: the extractor must quote both numbers, the
normalizer must key both claims the same way *and keep them apart*, and the
checker must be given both.

Nothing here resolves the disagreement, and that is the product decision the
tests are protecting: two sources that differ are a finding, and a system that
picks a winner silently reports one number with the other one deleted.
"""

from __future__ import annotations

from sqlalchemy import select

from app.core.enums import ContradictionResolution, ReportSectionKind, RunStatus
from app.db.models.evidence import ClaimRow, ContradictionRow
from app.db.models.report import ReportRow, ReportSectionRow
from app.sources.untrusted import BEGIN_MARKER
from tests.scenarios import story
from tests.scenarios.catalogue import Scenario, covers
from tests.scenarios.world import World, queue_run, run_until, scripted_dns
from tests.support.worker import read_row, settled

TERMINAL = (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)

CHEAP = "Provider A charges $4.10 per H100 GPU-hour."
DEAR = "Provider A charges $6.80 per H100 GPU-hour."


def disputing_world(make_world, **overrides: object) -> World:
    """One subtask, two pages, and two claims about the same number."""
    brain = story.ordinary_run()
    brain.on(story.SourceSelection, story.choosing(story.PRICE_LIST.url, story.DISPUTED_REVIEW.url))
    brain.on(
        story.EvidenceOutput,
        story.quoting(story.QUOTES[story.PRICE_LIST.url], story.QUOTES[story.DISPUTED_REVIEW.url]),
    )
    brain.on(story.ClaimsOutput, story.claiming_each((CHEAP, "$4.10"), (DEAR, "$6.80")))
    brain.on(story.ContradictionOutput, story.contradicting())
    return make_world(
        web=story.web(story.PRICE_LIST, story.DISPUTED_REVIEW), brain=brain, **overrides
    )


@covers(Scenario.CONTRADICTORY_SOURCES)
async def test_two_sources_that_disagree_become_two_claims_and_one_contradiction(
    make_world, database
):
    """The whole chain, from two pages to a row a reader can open.

    Two claims, because they assert different values; one contradiction, because
    they assert them about the same thing. Either half alone is the bug: one
    claim means the second number was silently discarded, and no contradiction
    means the reader is shown two numbers and left to notice.
    """
    world = disputing_world(make_world)

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, *TERMINAL))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.COMPLETED, row.error
    assert row.source_count == 2
    assert row.claim_count == 2, (
        "two sources asserting different values are two claims; collapsing them "
        "onto one id deletes a finding"
    )
    assert row.contradiction_count == 1

    async with database.session() as session:
        claims = (
            (await session.execute(select(ClaimRow).where(ClaimRow.run_id == run.id)))
            .scalars()
            .all()
        )
        found = (
            await session.execute(select(ContradictionRow).where(ContradictionRow.run_id == run.id))
        ).scalar_one()

    assert {claim.object_value for claim in claims} == {"$4.10", "$6.80"}
    assert len({claim.normalized_key for claim in claims}) == 1, (
        "one key, because the checker groups by it and a key held by a single "
        "claim cannot disagree with anything"
    )
    assert {found.claim_a_id, found.claim_b_id} == {claim.id for claim in claims}
    assert {found.value_a, found.value_b} == {"$4.10", "$6.80"}, (
        "the row carries both numbers, so a reader is shown the disagreement rather "
        "than told that one exists"
    )
    assert ContradictionResolution(found.resolution) is ContradictionResolution.UNRESOLVED
    assert found.resolved_by is None, "nothing in this system resolves a contradiction"


@covers(Scenario.CONTRADICTORY_SOURCES)
async def test_the_writer_is_told_both_numbers_and_the_section_it_writes_is_stored(
    make_world, database
):
    """How a disagreement reaches a reader, in the two halves it is made of.

    Evidence and References are assembled from rows; a contradictions section is
    not, because what to say about a disagreement is prose. So the system's
    contract is that the synthesizer is *given* both sides - inside the
    delimited block, like every other piece of retrieved text - and that the
    section it writes is stored as a section of the report.

    Asserting the prompt is the half that matters: a writer that is never told
    cannot mention it, and no amount of scripting the answer would reveal that.
    """
    written = story.report("Sources disagree: $4.10 [1] against $6.80 [2].")
    world = disputing_world(make_world)
    world.brain.on(
        story.ReportOutput,
        story.ReportOutput(
            title=written.title,
            sections=(
                *written.sections,
                story.SectionOutput(
                    kind=ReportSectionKind.CONTRADICTIONS,
                    heading="Where sources disagree",
                    content_md="One page lists $4.10 [1]; another bills $6.80 [2].",
                ),
            ),
        ),
    )

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, *TERMINAL))

    assert RunStatus((await read_row(database, run.id)).status) is RunStatus.COMPLETED

    prompt = world.brain.prompts_for(story.ReportOutput)[0]
    assert "$4.10" in prompt and "$6.80" in prompt
    assert "Report both sides" in prompt, "the writer is told not to pick one"
    assert BEGIN_MARKER in prompt, "and told them as data, not as instructions"

    async with database.session() as session:
        report = (
            await session.execute(select(ReportRow).where(ReportRow.run_id == run.id))
        ).scalar_one()
        sections = (
            (
                await session.execute(
                    select(ReportSectionRow)
                    .where(ReportSectionRow.report_id == report.id)
                    .order_by(ReportSectionRow.ordinal)
                )
            )
            .scalars()
            .all()
        )

    written_section = next(
        section for section in sections if section.kind == ReportSectionKind.CONTRADICTIONS.value
    )
    assert "4.10" in written_section.content_md and "6.80" in written_section.content_md
    # FR-9's ordering is the enum's declaration order, and the assembler applies
    # it to the sections the model wrote as well as to the two it builds itself.
    # Evidence therefore precedes Contradictions, whatever order they arrived in.
    declared = [kind.value for kind in ReportSectionKind]
    ordered = [section.kind for section in sections]
    assert ordered == sorted(ordered, key=declared.index)
    assert ReportSectionKind.EVIDENCE.value in ordered
    assert ReportSectionKind.REFERENCES.value == ordered[-1], "the reference list closes a report"


@covers(Scenario.CONTRADICTORY_SOURCES)
async def test_two_sources_that_agree_produce_no_contradiction_and_no_model_call(
    make_world, database
):
    """The negative, and it is not a formality.

    Corroboration and disagreement are the same shape from one step away - two
    sources, one subject - and the only thing that separates them is the value.
    A checker that fired on agreement would turn every well-supported claim into
    a dispute, so this asserts that the round makes no contradiction call at all.
    """
    brain = story.ordinary_run()
    brain.on(story.SourceSelection, story.choosing(story.PRICE_LIST.url, story.MARKET_REVIEW.url))
    brain.on(story.EvidenceOutput, story.quoting(*story.QUOTES.values()))
    world = make_world(web=story.web(), brain=brain)

    with scripted_dns():
        run = await queue_run(world)
        await run_until(world, settled(world.harness, database, run.id, *TERMINAL))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.COMPLETED, row.error
    assert row.source_count == 2
    assert row.claim_count == 1, "the same assertion from two pages is one corroborated claim"
    assert row.contradiction_count == 0
    assert world.brain.calls_for(story.ContradictionOutput) == 0, (
        "a key held by one claim cannot disagree with anything, so asking a model "
        "about it is a call spent to be told so"
    )
