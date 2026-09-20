"""A research run against the open internet. Nothing is scripted but embeddings.

The last substitution removed. `test_live_model` keeps the scripted socket, so
the corpus is two pages a fixture wrote and every hostname resolves to one
address that never receives a packet. This one uses the real search provider,
the real guarded HTTP client, real DNS and real pages - which means it is also
the only test that exercises the SSRF guard's four layers against hosts nobody
here chose, and the parser against HTML nobody here wrote.

**It is not deterministic and it is not fast.** The web changes, so what it
asserts is that the *chain* holds: whatever the run found, every citation leads
to a claim, to a span, to a document this run actually fetched, at offsets that
still hold the quoted words. It does not assert what the answer says.

**It costs money twice** - the model and the search provider - and it reaches
the open internet from whatever machine runs it.

    OPENAI_API_KEY=sk-... TAVILY_API_KEY=tvly-... AETHER_LIVE_WEB=1 \\
      pytest tests/scenarios/test_live_web.py -s

Embeddings stay scripted and the dense arm stays off, exactly as in the rest of
the scenario suite: the machine this was written on has no pgvector, so
retrieval runs its lexical arm. A deployment with the extension does more work
per run than this measures.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest
from sqlalchemy import select

from app.agents.factory import ResearchDependencies
from app.core.enums import RunStatus
from app.db.models.evidence import ClaimRow, EvidenceRow
from app.db.models.report import CitationRow, ReportRow, ReportSectionRow
from app.db.models.source import DocumentRow, SourceRow
from tests.scenarios import live, story, world
from tests.scenarios.world import queue_run, run_until
from tests.support.worker import read_row, settled

#: Deliberately a question with stable, well-published answers, so a run that
#: finds nothing is a signal about the system rather than about the topic.
QUESTION = (
    "What is the PagedAttention technique used in the vLLM inference server, "
    "and what problem does it solve?"
)

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("AETHER_LIVE_WEB") != "1",
        reason="live web run: set AETHER_LIVE_WEB=1 (spends money and reaches the internet)",
    ),
    pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="live web run needs OPENAI_API_KEY",
    ),
    pytest.mark.skipif(
        live.search_provider_key() is None,
        reason="live web run needs TAVILY_API_KEY or BRAVE_API_KEY",
    ),
]


@pytest.fixture
def live_internet(monkeypatch):
    """Real model, real socket, real DNS. Only embeddings stay scripted."""
    setting, value = live.search_provider_key()

    original = world.build_dependencies

    def dependencies(
        settings, *, gateway, database, storage, transport, tool_recorder
    ) -> ResearchDependencies:
        # `transport=None` is what every deployment passes: `build_toolbelt`
        # then builds the ordinary guarded client over a real socket, instead of
        # the mock the scenario suite hands it.
        return original(
            settings.model_copy(update={setting: value}),
            gateway=gateway,
            database=database,
            storage=storage,
            transport=None,
            tool_recorder=tool_recorder,
        )

    monkeypatch.setattr(world, "gateway_over", live.real_gateway)
    monkeypatch.setattr(world, "build_dependencies", dependencies)


async def test_a_run_against_the_open_internet_produces_a_grounded_report(
    live_internet, make_world, database
):
    # No `scripted_dns()`: resolution is real, so the SSRF guard resolves real
    # names and checks every address it gets back, which is the half of it that
    # a scripted resolver cannot exercise.
    built = make_world(web=story.web(), brain=story.ordinary_run())

    run = await queue_run(built, question=QUESTION)
    await run_until(built, settled(built.harness, database, run.id, RunStatus.COMPLETED))

    row = await read_row(database, run.id)
    print(
        f"\nlive web run: {row.status} · {row.source_count} sources · {row.claim_count} claims "
        f"· ${row.total_cost_usd} · {row.total_tokens} tokens"
    )

    assert RunStatus(row.status) is RunStatus.COMPLETED, row.error
    assert row.source_count > 0, "search returned nothing the researcher could use"

    async with database.session() as session:
        sources = (
            (await session.execute(select(SourceRow).where(SourceRow.run_id == run.id)))
            .scalars()
            .all()
        )
        for source in sources:
            print(f"  source: {source.url}")
            # Real URLs, on real hosts, fetched through the guard.
            assert urlsplit(source.url).scheme in {"http", "https"}
            assert urlsplit(source.url).hostname, f"a source with no host: {source.url}"

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
        print(f"  sections: {[s.kind for s in sections]}")
        print(f"  citations: {len(citations)}")

        assert sections, "a completed run produced a report with no sections"

        fetched = {source.id for source in sources}
        for citation in citations:
            claim = await session.get(ClaimRow, citation.claim_id)
            assert claim is not None and claim.run_id == run.id

            # The point of the whole system: a citation in a report about the
            # open web leads back to a page this run really retrieved.
            assert citation.source_id in fetched, (
                f"citation {citation.ordinal} points at a source this run never fetched"
            )

            spans = (
                (await session.execute(select(EvidenceRow).where(EvidenceRow.claim_id == claim.id)))
                .scalars()
                .all()
            )
            assert spans, f"claim {claim.id} is cited but carries no evidence"

            for span in spans:
                document = await session.get(DocumentRow, span.document_id)
                assert document is not None
                quoted = document.normalized_content[span.span_start : span.span_end]
                assert quoted == span.span_text, (
                    f"span drifted: stored {span.span_text!r}, document holds {quoted!r}"
                )
