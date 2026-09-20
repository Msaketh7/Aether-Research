"""Scenario 1, driven by a real model instead of a scripted one.

Opt-in, and skipped everywhere by default. Every other test in this repository
answers its model calls from a script, which is the only way to make them
deterministic and free - but it also means the nine agents' prompts and parsers
had never met real model output. This is the test that closes that gap, and it
is the one to run after changing a prompt, an output schema or a parser.

**What it swaps, and what it does not.** Only the chat provider. The socket
stays scripted, so no search-provider key is needed and the corpus is still the
two pages `story` serves; embeddings stay scripted too, because the dense arm
needs pgvector and the machine this was written on has none. Everything else is
the vertical slice the rest of the scenario suite drives: queue, worker, lease,
graph, nine real agents, ingestion, lexical retrieval, the evidence projection,
report assembly and the citation check, against real Postgres.

**It costs money and it is not deterministic.** About three cents and a minute
and a half per run at the model below, and the claim count varies run to run -
which is the point, and why its assertions are about the *chain* holding rather
than about a number of claims. The first four runs produced 5, 3, 4 and 1
claims; every one of them produced a report whose citations resolved.

    OPENAI_API_KEY=sk-... AETHER_LIVE_MODEL=1 \\
      pytest tests/scenarios/test_live_model.py -s
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select

from app.core.enums import RunStatus
from app.db.models.evidence import ClaimRow, EvidenceRow
from app.db.models.report import CitationRow, ReportRow, ReportSectionRow
from app.db.models.source import DocumentRow, SourceRow
from tests.scenarios import live, story, world
from tests.scenarios.world import queue_run, run_until, scripted_dns
from tests.support.worker import read_row, settled

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("AETHER_LIVE_MODEL") != "1",
        reason="live model run: set AETHER_LIVE_MODEL=1 and OPENAI_API_KEY (costs money)",
    ),
    pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="live model run needs OPENAI_API_KEY",
    ),
]


@pytest.fixture
def live_model(monkeypatch):
    """Replace the scripted chat provider at the one seam that builds a gateway."""
    monkeypatch.setattr(world, "gateway_over", live.real_gateway)


async def test_a_real_model_produces_a_report_whose_citations_resolve(
    live_model, make_world, database
):
    built = make_world(web=story.web(), brain=story.ordinary_run())

    with scripted_dns():
        run = await queue_run(built, question=story.QUESTION)
        await run_until(built, settled(built.harness, database, run.id, RunStatus.COMPLETED))

    row = await read_row(database, run.id)
    print(
        f"\nlive run: {row.status} · {row.source_count} sources · {row.claim_count} claims "
        f"· ${row.total_cost_usd} · {row.total_tokens} tokens"
    )

    assert RunStatus(row.status) is RunStatus.COMPLETED, row.error
    assert row.error is None
    # A real model's output varies, so this asserts that the run found
    # *something* rather than a count it cannot promise.
    assert row.source_count > 0, "the researcher selected no source from the scripted corpus"
    assert row.claim_count > 0, "nothing survived extraction and verification"

    # The ledger priced the call. Worth asserting because a provider reports the
    # dated snapshot an alias resolved to, and pricing that missed would read as
    # "not measured" and stop discovery early - which is what it used to do.
    assert row.total_cost_usd > 0, "a live run recorded no cost; is the model priced?"
    assert row.total_tokens > 0

    async with database.session() as session:
        report = (
            await session.execute(select(ReportRow).where(ReportRow.run_id == run.id))
        ).scalar_one()
        sections = (
            (
                await session.execute(
                    select(ReportSectionRow).where(ReportSectionRow.report_id == report.id)
                )
            )
            .scalars()
            .all()
        )
        citations = (
            (
                await session.execute(
                    select(CitationRow).where(
                        CitationRow.report_section_id.in_([s.id for s in sections])
                    )
                )
            )
            .scalars()
            .all()
        )

        assert sections, "a completed run produced a report with no sections"
        assert citations, "a completed run produced a report with no citations"

        # The chain, walked one link at a time, for every citation rather than
        # the first: with a real model the interesting failure is the one
        # citation in six whose span drifted.
        for citation in citations:
            claim = await session.get(ClaimRow, citation.claim_id)
            assert claim is not None and claim.run_id == run.id

            evidence = (
                (await session.execute(select(EvidenceRow).where(EvidenceRow.claim_id == claim.id)))
                .scalars()
                .all()
            )
            assert evidence, f"claim {claim.id} is cited but carries no evidence"

            for span in evidence:
                document = await session.get(DocumentRow, span.document_id)
                assert document is not None

                # The offsets are the promise: re-read the document at them and
                # the stored span must still be there. This is exactly what the
                # citation validator does, done again from the database.
                quoted = document.normalized_content[span.span_start : span.span_end]
                assert quoted == span.span_text, (
                    f"span drifted: stored {span.span_text!r}, "
                    f"document holds {quoted!r} at {span.span_start}:{span.span_end}"
                )

            source = await session.get(SourceRow, citation.source_id)
            assert source is not None
            assert source.url in {story.PRICE_LIST.url, story.MARKET_REVIEW.url}, (
                f"a citation points at {source.url}, which this run never fetched"
            )
