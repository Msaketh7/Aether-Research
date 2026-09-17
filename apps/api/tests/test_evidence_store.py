"""The evidence chain in Postgres: projecting a run, and reading it back.

Real database throughout, because everything being asserted here is a property
of the SQL: that a second projection rewrites rows rather than doubling them,
that a count is recomputed rather than incremented, that a resolution a person
set is not overwritten by a projection that only ever writes "unresolved", and
that a read scoped to one user returns nothing for another's run.

The state being projected is built by hand rather than run through the graph -
``test_agents_end_to_end`` already runs the graph, and what is under test here
is the step after it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa

from app.agents.schemas import MAX_EVIDENCE_PER_CLAIM as graph_cap
from app.core.enums import ClaimStatus, EvidenceStance, ResearchMode, RunStatus, SourceType
from app.db.models.research import ResearchRunRow
from app.db.models.source import DocumentRow, SourceRow
from app.db.repositories.evidence import MAX_EVIDENCE_PER_CLAIM as repository_cap
from app.db.repositories.evidence import SqlAlchemyEvidenceRepository
from app.db.repositories.user import UserRepository
from app.db.session import Database
from app.evidence.dedup import cluster_identity
from app.evidence.projection import UNKNOWN_MODEL, EvidenceProjector, evidence_link_id
from app.sources.credibility import assess
from tests.support import agents as fake

pytestmark = pytest.mark.anyio

WIRE = (
    "The company said quarterly data center revenue reached thirty five point six "
    "billion dollars, up from twenty two point six billion a year earlier, as "
    "demand for inference accelerators continued to outstrip supply everywhere."
)
OTHER = (
    "A research firm argued in a note that inference pricing will fall next year "
    "as new capacity arrives, and advised clients to delay long term commitments "
    "until the second half of the year."
)


@pytest.fixture
async def owner(database: Database) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with database.session() as session:
        await UserRepository(session).ensure(user_id, f"{user_id}@example.test")
    return user_id


@pytest.fixture
async def run_id(database: Database, owner: uuid.UUID) -> uuid.UUID:
    return await seed_run(database, owner)


async def seed_run(database: Database, user_id: uuid.UUID) -> uuid.UUID:
    async with database.session() as session:
        run = ResearchRunRow(
            user_id=user_id,
            title="Inference pricing",
            question="What does inference cost?",
            mode=ResearchMode.DEEP.value,
            status=RunStatus.RESEARCHING.value,
        )
        session.add(run)
        await session.flush()
        return run.id


async def seed_source(
    database: Database,
    run_id: uuid.UUID,
    name: str,
    *,
    url: str | None = None,
    digest: str | None = None,
    excerpt: str = WIRE,
    source_type: SourceType = SourceType.WEB,
    domain: str = "example.test",
    minutes: int = 0,
) -> tuple[uuid.UUID, uuid.UUID]:
    """One source and one document for it. Returns ``(source_id, document_id)``.

    Written as rows rather than through ingestion: ingestion needs bytes, a
    parser and object storage, and all this needs is a source with a hash and an
    excerpt. The pipeline that really writes these has its own suite - including
    the assertion that it stores the credibility shape used here.
    """
    accessed = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC) + dt.timedelta(minutes=minutes)
    credibility = assess(source_type, domain)
    async with database.session() as session:
        source = SourceRow(
            run_id=run_id,
            url=url or f"https://{name}.test/story",
            canonical_url=url or f"https://{name}.test/story",
            domain=domain,
            source_type=source_type.value,
            title=f"Report from {name}",
            publisher=domain,
            accessed_at=accessed,
            content_hash=digest or f"hash-{name}",
            credibility_score=credibility.score,
            credibility_metadata=credibility.as_metadata(),
            excerpt=excerpt,
        )
        session.add(source)
        await session.flush()
        document = DocumentRow(
            source_id=source.id,
            normalized_content=excerpt,
            content_hash=digest or f"hash-{name}",
        )
        session.add(document)
        await session.flush()
        return source.id, document.id


def state_with(run_id: uuid.UUID, **overrides: object) -> dict[str, object]:
    return fake.state(research_id=run_id, **overrides)


async def count(database: Database, table: str, run_id: uuid.UUID | None = None) -> int:
    where = " WHERE run_id = :run_id" if run_id else ""
    async with database.session() as session:
        result = await session.execute(
            sa.text(f"SELECT count(*) FROM {table}{where}"),  # noqa: S608 - fixed table names
            {"run_id": run_id} if run_id else {},
        )
        return int(result.scalar_one())


@pytest.fixture
async def projector(database: Database) -> AsyncIterator[EvidenceProjector]:
    yield EvidenceProjector(database)


# --- projecting -----------------------------------------------------------------


async def test_a_run_becomes_claims_evidence_and_contradictions(
    database: Database, projector: EvidenceProjector, run_id: uuid.UUID
):
    source_a, document_a = await seed_source(database, run_id, "a")
    source_b, document_b = await seed_source(database, run_id, "b", digest="other", excerpt=OTHER)
    first = fake.evidence("ev-1", quote=WIRE[:80])
    second = fake.evidence("ev-2", quote=OTHER[:80], stance=EvidenceStance.REFUTES)
    state = state_with(
        run_id,
        evidence=[
            first.model_copy(update={"source_id": source_a, "document_id": document_a}),
            second.model_copy(update={"source_id": source_b, "document_id": document_b}),
        ],
        claims=[
            fake.claim("claim-1", evidence_ids=(first.id,), object_value="$35.6B"),
            fake.claim(
                "claim-2",
                text="Inference prices are expected to fall.",
                key="market | inference price direction | 2027",
                evidence_ids=(second.id,),
                object_value="fall",
            ),
        ],
    )

    projected = await projector.record(state)

    assert projected.claims == 2
    assert projected.evidence == 2
    assert await count(database, "claims", run_id) == 2
    assert await count(database, "evidence") == 2


async def test_projecting_the_same_run_twice_rewrites_rather_than_duplicates(
    database: Database, projector: EvidenceProjector, run_id: uuid.UUID
):
    # A resumed run re-projects everything its checkpoint holds: the nodes that
    # already ran are not re-run, but their output is written again.
    source_id, document_id = await seed_source(database, run_id, "a")
    span = fake.evidence("ev-1", quote=WIRE[:80]).model_copy(
        update={"source_id": source_id, "document_id": document_id}
    )
    claim = fake.claim("claim-1", evidence_ids=(span.id,))
    await projector.record(state_with(run_id, evidence=[span], claims=[claim]))

    async with database.session() as session:
        first_seen = (
            await session.execute(sa.text("SELECT first_seen_at FROM claims"))
        ).scalar_one()

    verified = claim.model_copy(update={"status": ClaimStatus.VERIFIED, "confidence": 0.9})
    await projector.record(state_with(run_id, evidence=[span], claims=[verified]))

    assert await count(database, "claims", run_id) == 1
    assert await count(database, "evidence") == 1
    async with database.session() as session:
        row = (
            await session.execute(sa.text("SELECT status, confidence, first_seen_at FROM claims"))
        ).one()
    assert row.status == "verified", "a re-scored claim is the same claim, revised"
    assert float(row.confidence) == pytest.approx(0.9)
    assert row.first_seen_at == first_seen, "when the run first believed it does not change"


async def test_one_span_behind_two_claims_is_two_rows_that_keep_their_identity(
    database: Database, projector: EvidenceProjector, run_id: uuid.UUID
):
    source_id, document_id = await seed_source(database, run_id, "a")
    span = fake.evidence("ev-1", quote=WIRE[:80]).model_copy(
        update={"source_id": source_id, "document_id": document_id}
    )
    claims = [
        fake.claim("claim-1", evidence_ids=(span.id,)),
        fake.claim(
            "claim-2",
            text="Demand outstrips supply.",
            key="market | demand versus supply | 2026",
            evidence_ids=(span.id,),
        ),
    ]

    await projector.record(state_with(run_id, evidence=[span], claims=claims))

    assert await count(database, "evidence") == 2, (
        "evidence.claim_id is a single foreign key, so the row is the link and "
        "one quote cited by two claims is two of them"
    )
    async with database.session() as session:
        ids = set(
            (await session.execute(sa.text("SELECT id FROM evidence"))).scalars(),
        )
    assert ids == {evidence_link_id(claim.id, span.id) for claim in claims}


async def test_corroboration_counts_clusters_not_copies(
    database: Database, projector: EvidenceProjector, run_id: uuid.UUID
):
    # The same wire story on three sites, plus one independent account.
    original, doc_1 = await seed_source(database, run_id, "wire", digest="same")
    reprint, doc_2 = await seed_source(database, run_id, "reprint", digest="same", minutes=1)
    third, doc_3 = await seed_source(database, run_id, "third", digest="same", minutes=2)
    independent, doc_4 = await seed_source(
        database, run_id, "independent", digest="own", excerpt=OTHER, minutes=3
    )
    spans = [
        fake.evidence(f"ev-{i}", quote=WIRE[:80], source_name=f"s{i}").model_copy(
            update={"source_id": source_id, "document_id": document_id}
        )
        for i, (source_id, document_id) in enumerate(
            [(original, doc_1), (reprint, doc_2), (third, doc_3), (independent, doc_4)]
        )
    ]
    claim = fake.claim("claim-1", evidence_ids=tuple(span.id for span in spans))

    await projector.record(state_with(run_id, evidence=spans, claims=[claim]))

    async with database.session() as session:
        corroboration = (
            await session.execute(sa.text("SELECT corroboration_count FROM claims"))
        ).scalar_one()
    assert corroboration == 2, (
        "three copies of one story and one independent account is two sources, "
        "not four - counting the copies is confidence the evidence never earned"
    )


async def test_a_duplicate_source_is_recorded_with_the_rule_that_found_it(
    database: Database, projector: EvidenceProjector, run_id: uuid.UUID
):
    original, _ = await seed_source(database, run_id, "wire", digest="same")
    reprint, _ = await seed_source(database, run_id, "reprint", digest="same", minutes=1)

    await projector.record(state_with(run_id))

    async with database.session() as session:
        rows = (
            await session.execute(
                sa.text(
                    "SELECT id, dedup_cluster_id, dedup_reason FROM sources ORDER BY accessed_at"
                )
            )
        ).all()
    assert [row.id for row in rows] == [original, reprint]
    assert {row.dedup_cluster_id for row in rows} == {cluster_identity(run_id, original)}, (
        "both members carry the cluster, so the page can group them without a join"
    )
    assert {row.dedup_reason for row in rows} == {"exact_hash"}


async def test_counts_are_recomputed_rather_than_incremented(
    database: Database, projector: EvidenceProjector, run_id: uuid.UUID
):
    source_id, document_id = await seed_source(database, run_id, "a")
    span = fake.evidence("ev-1", quote=WIRE[:80]).model_copy(
        update={"source_id": source_id, "document_id": document_id}
    )
    claim = fake.claim("claim-1", evidence_ids=(span.id,))
    state = state_with(run_id, evidence=[span], claims=[claim])

    await projector.record(state)
    await projector.record(state)

    async with database.session() as session:
        run = (
            await session.execute(
                sa.text("SELECT claim_count, contradiction_count FROM research_runs")
            )
        ).one()
        source_claims = (
            await session.execute(sa.text("SELECT claim_count FROM sources"))
        ).scalar_one()
    assert run.claim_count == 1, "a count that doubles on a resume is a count nobody can reconcile"
    assert run.contradiction_count == 0
    assert source_claims == 1


async def test_a_source_that_supported_nothing_counts_zero_rather_than_nothing(
    database: Database, projector: EvidenceProjector, run_id: uuid.UUID
):
    await seed_source(database, run_id, "unused")

    await projector.record(state_with(run_id))

    async with database.session() as session:
        value = (await session.execute(sa.text("SELECT claim_count FROM sources"))).scalar_one()
    assert value == 0, "it was read and produced no claim, which is a measurement"


async def test_a_span_from_a_checkpoint_without_a_model_is_recorded_as_unknown(
    database: Database, projector: EvidenceProjector, run_id: uuid.UUID
):
    source_id, document_id = await seed_source(database, run_id, "a")
    span = fake.evidence("ev-1", quote=WIRE[:80], model="").model_copy(
        update={"source_id": source_id, "document_id": document_id}
    )

    await projector.record(
        state_with(run_id, evidence=[span], claims=[fake.claim("claim-1", evidence_ids=(span.id,))])
    )

    async with database.session() as session:
        model = (
            await session.execute(sa.text("SELECT extractor_model FROM evidence"))
        ).scalar_one()
    assert model == UNKNOWN_MODEL, "attributing it to the role's model would name one that may "
    "never have produced it"


# --- contradictions ---------------------------------------------------------------


async def test_a_contradiction_is_written_unresolved_with_both_values(
    database: Database, projector: EvidenceProjector, run_id: uuid.UUID
):
    source_a, document_a = await seed_source(database, run_id, "a")
    source_b, document_b = await seed_source(database, run_id, "b", digest="other", excerpt=OTHER)
    first = fake.evidence("ev-1", quote=WIRE[:80]).model_copy(
        update={"source_id": source_a, "document_id": document_a}
    )
    second = fake.evidence("ev-2", quote=OTHER[:80]).model_copy(
        update={"source_id": source_b, "document_id": document_b}
    )
    key = "provider a | h100 price per gpu hour | 2026"
    claim_a = fake.claim(
        "claim-1",
        text="H100 inference costs $4.10 per GPU-hour.",
        key=key,
        evidence_ids=(first.id,),
        object_value="$4.10",
    )
    claim_b = fake.claim(
        "claim-2",
        text="H100 inference costs $2.80 per GPU-hour.",
        key=key,
        evidence_ids=(second.id,),
        object_value="$2.80",
    )

    await projector.record(
        state_with(
            run_id,
            evidence=[first, second],
            claims=[claim_a, claim_b],
            contradictions=[fake.contradiction("c-1", key=key)],
        )
    )

    async with database.session() as session:
        row = (
            await session.execute(
                sa.text(
                    "SELECT value_a, value_b, source_a_id, source_b_id, resolution, resolved_by "
                    "FROM contradictions"
                )
            )
        ).one()
    assert (row.value_a, row.value_b) == ("$4.10", "$2.80"), (
        "a conflict a reader cannot see the two sides of is not surfaced"
    )
    assert {row.source_a_id, row.source_b_id} == {source_a, source_b}
    assert row.resolution == "unresolved"
    assert row.resolved_by is None


async def test_a_resolution_someone_recorded_survives_the_next_projection(
    database: Database, projector: EvidenceProjector, run_id: uuid.UUID
):
    source_a, document_a = await seed_source(database, run_id, "a")
    source_b, document_b = await seed_source(database, run_id, "b", digest="other", excerpt=OTHER)
    first = fake.evidence("ev-1", quote=WIRE[:80]).model_copy(
        update={"source_id": source_a, "document_id": document_a}
    )
    second = fake.evidence("ev-2", quote=OTHER[:80]).model_copy(
        update={"source_id": source_b, "document_id": document_b}
    )
    key = "provider a | h100 price per gpu hour | 2026"
    claims = [
        fake.claim("claim-1", key=key, evidence_ids=(first.id,), object_value="$4.10"),
        fake.claim(
            "claim-2",
            text="H100 inference costs $2.80 per GPU-hour.",
            key=key,
            evidence_ids=(second.id,),
            object_value="$2.80",
        ),
    ]
    state = state_with(
        run_id,
        evidence=[first, second],
        claims=claims,
        contradictions=[fake.contradiction("c-1", key=key)],
    )
    await projector.record(state)

    async with database.session() as session:
        await session.execute(
            sa.text(
                "UPDATE contradictions SET resolution = 'both_valid_in_context', "
                "resolved_by = 'analyst'"
            )
        )
        await session.commit()

    await projector.record(state)

    async with database.session() as session:
        row = (
            await session.execute(sa.text("SELECT resolution, resolved_by FROM contradictions"))
        ).one()
    assert row.resolution == "both_valid_in_context", (
        "FR-7 cuts both ways: the system never resolves silently, and it never "
        "un-resolves what a person decided either"
    )
    assert row.resolved_by == "analyst"


async def test_a_contradiction_whose_claims_are_gone_is_not_written(
    database: Database, projector: EvidenceProjector, run_id: uuid.UUID
):
    await projector.record(
        state_with(run_id, contradictions=[fake.contradiction("c-1")], claims=[], evidence=[])
    )

    assert await count(database, "contradictions", run_id) == 0, (
        "a row pointing at claims that do not exist could not be displayed, and "
        "the foreign key would refuse it anyway"
    )


async def test_the_summary_counts_what_was_written_not_what_state_held(
    database: Database, projector: EvidenceProjector, run_id: uuid.UUID
):
    source_id, document_id = await seed_source(database, run_id, "a")
    span = fake.evidence("ev-1", quote=WIRE[:80]).model_copy(
        update={"source_id": source_id, "document_id": document_id}
    )
    # A claim citing one span that is in state and one that was trimmed out of
    # the checkpoint: the second can produce no row, because there is nothing
    # for it to quote.
    claim = fake.claim("claim-1", evidence_ids=(span.id, uuid.uuid4()))

    projected = await projector.record(state_with(run_id, evidence=[span], claims=[claim]))

    assert projected.evidence == 1
    assert await count(database, "evidence") == 1, "the summary is the row count, not the intent"


# --- reading back -----------------------------------------------------------------


async def test_the_sources_page_serves_real_rows_with_their_clusters(
    database: Database, projector: EvidenceProjector, run_id: uuid.UUID, owner: uuid.UUID
):
    original, _ = await seed_source(database, run_id, "wire", digest="same")
    reprint, _ = await seed_source(database, run_id, "reprint", digest="same", minutes=1)
    await seed_source(database, run_id, "other", digest="own", excerpt=OTHER, minutes=2)
    await projector.record(state_with(run_id))

    async with database.session() as session:
        page = await SqlAlchemyEvidenceRepository(session).sources_page(
            run_id, user_id=owner, limit=10
        )

    assert page.total == 3
    assert [source.id for source in page.sources] == [original, reprint, page.sources[2].id]
    assert len(page.clusters) == 1, "a source that duplicates nothing needs no cluster shown"
    assert page.clusters[0].primary_source_id == original
    assert page.clusters[0].duplicate_source_ids == [reprint]
    assert page.sources[0].relevance_score is None, "no relevance has been measured"
    assert page.sources[0].credibility_metadata.tier == "unknown", (
        "an unrecognised domain is scored as unknown, not as a neutral middle"
    )


async def test_the_sources_page_filters_by_type_and_pages_with_an_opaque_cursor(
    database: Database, projector: EvidenceProjector, run_id: uuid.UUID, owner: uuid.UUID
):
    for index in range(3):
        await seed_source(
            database,
            run_id,
            f"web-{index}",
            digest=f"d{index}",
            minutes=index,
            excerpt=f"{OTHER}{index}",
        )
    await seed_source(
        database,
        run_id,
        "filing",
        digest="sec",
        source_type=SourceType.SEC,
        domain="sec.gov",
        minutes=9,
    )
    await projector.record(state_with(run_id))

    async with database.session() as session:
        repository = SqlAlchemyEvidenceRepository(session)
        filings = await repository.sources_page(
            run_id, user_id=owner, limit=10, source_type=SourceType.SEC
        )
        first = await repository.sources_page(run_id, user_id=owner, limit=2)

    assert filings.total == 1
    assert filings.sources[0].credibility_metadata.is_primary, "a filing is the thing itself"
    assert filings.sources[0].source_type is SourceType.SEC
    assert len(first.sources) == 2
    assert first.next_cursor is not None
    assert first.total == 4, "the total counts what matches the filter, not the page"


async def test_the_evidence_page_returns_claims_with_their_spans_split_by_stance(
    database: Database, projector: EvidenceProjector, run_id: uuid.UUID, owner: uuid.UUID
):
    source_a, document_a = await seed_source(database, run_id, "a")
    source_b, document_b = await seed_source(database, run_id, "b", digest="other", excerpt=OTHER)
    supporting = fake.evidence("ev-1", quote=WIRE[:80]).model_copy(
        update={"source_id": source_a, "document_id": document_a}
    )
    refuting = fake.evidence("ev-2", quote=OTHER[:80], stance=EvidenceStance.REFUTES).model_copy(
        update={"source_id": source_b, "document_id": document_b}
    )
    claim = fake.claim("claim-1", evidence_ids=(supporting.id, refuting.id))

    await projector.record(state_with(run_id, evidence=[supporting, refuting], claims=[claim]))
    async with database.session() as session:
        page = await SqlAlchemyEvidenceRepository(session).evidence_page(
            run_id, user_id=owner, limit=10
        )

    assert page.total == 1
    [returned] = page.claims
    assert returned.subject == "provider a", "the triple comes from the key the normalizer wrote"
    assert returned.predicate == "h100 price per gpu hour"
    assert [span.span_text for span in returned.supporting] == [supporting.claim_text]
    assert [span.span_text for span in returned.refuting] == [refuting.claim_text]
    assert returned.supporting[0].span_start == supporting.span_start


async def test_the_evidence_page_filters_by_claim_status(
    database: Database, projector: EvidenceProjector, run_id: uuid.UUID, owner: uuid.UUID
):
    source_id, document_id = await seed_source(database, run_id, "a")
    span = fake.evidence("ev-1", quote=WIRE[:80]).model_copy(
        update={"source_id": source_id, "document_id": document_id}
    )
    claims = [
        fake.claim("claim-1", evidence_ids=(span.id,), status=ClaimStatus.VERIFIED),
        fake.claim(
            "claim-2",
            text="Demand outstrips supply.",
            key="market | demand versus supply | 2026",
            evidence_ids=(span.id,),
            status=ClaimStatus.CANDIDATE,
        ),
    ]
    await projector.record(state_with(run_id, evidence=[span], claims=claims))

    async with database.session() as session:
        page = await SqlAlchemyEvidenceRepository(session).evidence_page(
            run_id, user_id=owner, limit=10, status=ClaimStatus.VERIFIED
        )

    assert page.total == 1
    assert page.claims[0].status is ClaimStatus.VERIFIED


async def test_another_users_run_reads_as_empty_rather_than_as_someone_elses(
    database: Database, projector: EvidenceProjector, run_id: uuid.UUID
):
    source_id, document_id = await seed_source(database, run_id, "a")
    span = fake.evidence("ev-1", quote=WIRE[:80]).model_copy(
        update={"source_id": source_id, "document_id": document_id}
    )
    await projector.record(
        state_with(run_id, evidence=[span], claims=[fake.claim("claim-1", evidence_ids=(span.id,))])
    )
    stranger = uuid.uuid4()
    async with database.session() as session:
        await UserRepository(session).ensure(stranger, f"{stranger}@example.test")

    async with database.session() as session:
        repository = SqlAlchemyEvidenceRepository(session)
        sources = await repository.sources_page(run_id, user_id=stranger, limit=10)
        evidence = await repository.evidence_page(run_id, user_id=stranger, limit=10)

    assert (sources.sources, sources.total) == ([], 0)
    assert (evidence.claims, evidence.total) == ([], 0), (
        "ownership is a WHERE clause, so another user's run has no rows at all"
    )


async def test_a_cursor_pointing_at_a_row_that_is_gone_ends_the_traversal(
    database: Database, projector: EvidenceProjector, run_id: uuid.UUID, owner: uuid.UUID
):
    await seed_source(database, run_id, "a")
    await projector.record(state_with(run_id))

    async with database.session() as session:
        page = await SqlAlchemyEvidenceRepository(session).sources_page(
            run_id, user_id=owner, limit=10, after_id=uuid.uuid4()
        )

    assert page.sources == []
    assert page.next_cursor is None
    assert page.total == 1, "the total still describes the run, not the page"


async def test_the_page_bound_on_spans_matches_the_graphs_own_ceiling():
    """The repository mirrors the graph's cap so a page cannot be unbounded.

    Declared twice on purpose - the read model must not import an agent's
    schema - so the two are checked against each other here rather than left to
    drift into a page that silently drops a claim's last spans.
    """
    assert repository_cap == graph_cap
