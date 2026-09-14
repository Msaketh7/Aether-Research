"""The Postgres research repository.

Exercised directly rather than only through the API, because the properties
that matter most here are hard to reach from HTTP: keyset pagination when two
runs share a timestamp, the cursor pointing at a deleted row, and the fact that
ownership is a WHERE clause rather than a filter applied after fetching.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.enums import ResearchMode, RunStatus
from app.db.repositories.research import ABSOLUTE_MAX_ROWS, SqlAlchemyResearchRepository
from app.db.repositories.user import UserRepository
from app.research.schemas import ResearchRun, RunLimits, RunUsage
from tests.support.postgres import ProvisionedDatabase


@pytest.fixture
async def session(postgres: ProvisionedDatabase | None) -> AsyncIterator[AsyncSession]:
    if postgres is None:
        pytest.skip("no Postgres available")
    engine = create_async_engine(postgres.url, poolclass=sa.pool.NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
            await session.commit()
    finally:
        await engine.dispose()


@pytest.fixture
async def repository(session: AsyncSession) -> SqlAlchemyResearchRepository:
    return SqlAlchemyResearchRepository(session)


@pytest.fixture
async def owner(session: AsyncSession) -> uuid.UUID:
    user_id = uuid.uuid4()
    await UserRepository(session).ensure(user_id, "owner@example.com")
    await session.flush()
    return user_id


@pytest.fixture
async def stranger(session: AsyncSession) -> uuid.UUID:
    user_id = uuid.uuid4()
    await UserRepository(session).ensure(user_id, "stranger@example.com")
    await session.flush()
    return user_id


def make_run(user_id: uuid.UUID, **overrides: object) -> ResearchRun:
    settings = Settings(app_env="test")
    now = dt.datetime.now(dt.UTC)
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "parent_run_id": None,
        "title": "Inference comparison",
        "question": "Compare the major AI inference infrastructure companies.",
        "mode": ResearchMode.DEEP,
        "depth": 3,
        "domains": [],
        "date_range_start": None,
        "date_range_end": None,
        "status": RunStatus.QUEUED,
        "progress": 0.0,
        "limits": RunLimits.for_mode(ResearchMode.DEEP, settings),
        "usage": RunUsage(),
        "source_count": 0,
        "claim_count": 0,
        "contradiction_count": 0,
        "coverage_caveat": None,
        "has_report": False,
        "created_at": now,
        "started_at": None,
        "completed_at": None,
        "error": None,
    }
    values.update(overrides)
    return ResearchRun.model_validate(values)


# --- round trip -----------------------------------------------------------


async def test_a_run_survives_a_round_trip(
    repository: SqlAlchemyResearchRepository, owner: uuid.UUID
):
    run = make_run(
        owner,
        domains=["sec.gov", "arxiv.org"],
        date_range_start=dt.date(2026, 1, 1),
        depth=4,
        usage=RunUsage(iterations=2, sources=14, total_tokens=8400, cost_usd=1.2345),
    )
    await repository.add(run)

    loaded = await repository.get(run.id, user_id=owner)

    assert loaded is not None
    assert loaded.question == run.question
    assert loaded.domains == ["sec.gov", "arxiv.org"]
    assert loaded.date_range_start == dt.date(2026, 1, 1)
    assert loaded.depth == 4
    # Numeric(10, 4): money must not drift through a float round trip.
    assert loaded.usage.cost_usd == pytest.approx(1.2345)
    assert loaded.usage.total_tokens == 8400
    assert loaded.limits.max_iterations == run.limits.max_iterations


async def test_the_limits_in_force_are_frozen_with_the_run(
    repository: SqlAlchemyResearchRepository, owner: uuid.UUID
):
    """A later configuration change must not rewrite what a finished run was
    allowed to do."""
    tight = RunLimits(
        max_iterations=1,
        max_sources=5,
        max_search_queries=6,
        max_runtime_seconds=30,
        max_cost_usd=0.10,
    )
    run = make_run(owner, limits=tight)
    await repository.add(run)

    loaded = await repository.get(run.id, user_id=owner)

    assert loaded is not None
    assert loaded.limits.max_sources == 5
    assert loaded.limits.max_cost_usd == pytest.approx(0.10)


async def test_updating_a_run_persists_the_new_state(
    repository: SqlAlchemyResearchRepository, owner: uuid.UUID
):
    run = await repository.add(make_run(owner))
    finished = run.model_copy(
        update={
            "status": RunStatus.COMPLETED,
            "progress": 1.0,
            "completed_at": dt.datetime.now(dt.UTC),
        }
    )

    await repository.update(finished)
    loaded = await repository.get(run.id, user_id=owner)

    assert loaded is not None
    assert loaded.status is RunStatus.COMPLETED
    assert loaded.progress == 1.0
    assert loaded.completed_at is not None


async def test_updating_a_deleted_run_fails_rather_than_resurrecting_it(
    repository: SqlAlchemyResearchRepository, session: AsyncSession, owner: uuid.UUID
):
    run = await repository.add(make_run(owner))
    await repository.delete_for_user(owner)
    await session.flush()

    with pytest.raises(LookupError):
        await repository.update(run)


# --- ownership ------------------------------------------------------------


async def test_a_run_is_invisible_to_another_user(
    repository: SqlAlchemyResearchRepository, owner: uuid.UUID, stranger: uuid.UUID
):
    run = await repository.add(make_run(owner))

    assert await repository.get(run.id, user_id=stranger) is None
    assert await repository.get(run.id, user_id=owner) is not None


async def test_listing_never_crosses_users(
    repository: SqlAlchemyResearchRepository, owner: uuid.UUID, stranger: uuid.UUID
):
    await repository.add(make_run(owner))
    await repository.add(make_run(stranger))

    mine, _ = await repository.list_for_user(owner, limit=10)
    theirs, _ = await repository.list_for_user(stranger, limit=10)

    assert len(mine) == 1
    assert len(theirs) == 1
    assert mine[0].id != theirs[0].id


# --- pagination -----------------------------------------------------------


async def test_pagination_is_stable_when_timestamps_collide(
    repository: SqlAlchemyResearchRepository, owner: uuid.UUID
):
    """The reason the index is (created_at DESC, id DESC).

    Ordering by timestamp alone is not a total order. With runs created in the
    same instant - which happens under load - a paginating client would skip or
    repeat rows.
    """
    same_instant = dt.datetime.now(dt.UTC)
    created = [await repository.add(make_run(owner, created_at=same_instant)) for _ in range(6)]

    seen: list[uuid.UUID] = []
    cursor: uuid.UUID | None = None
    for _ in range(6):
        page, has_more = await repository.list_for_user(owner, limit=2, after_id=cursor)
        seen.extend(run.id for run in page)
        if not has_more:
            break
        cursor = page[-1].id

    assert len(seen) == len(created)
    assert len(set(seen)) == len(created), "a row was returned twice"
    assert set(seen) == {run.id for run in created}


async def test_a_cursor_pointing_at_a_removed_run_returns_nothing(
    repository: SqlAlchemyResearchRepository, session: AsyncSession, owner: uuid.UUID
):
    """Safer than restarting from the top, which would loop a client forever."""
    run = await repository.add(make_run(owner))
    await repository.delete_for_user(owner)
    await session.flush()

    page, has_more = await repository.list_for_user(owner, limit=10, after_id=run.id)

    assert page == []
    assert has_more is False


async def test_a_cursor_from_another_user_reveals_nothing(
    repository: SqlAlchemyResearchRepository, owner: uuid.UUID, stranger: uuid.UUID
):
    theirs = await repository.add(make_run(stranger))
    await repository.add(make_run(owner))

    page, _ = await repository.list_for_user(owner, limit=10, after_id=theirs.id)

    assert page == []


async def test_the_page_size_is_capped_in_the_repository_too(
    repository: SqlAlchemyResearchRepository, owner: uuid.UUID
):
    """Defence in depth: the API clamps as well, but a future caller may not."""
    await repository.add(make_run(owner))

    page, _ = await repository.list_for_user(owner, limit=10_000)

    assert len(page) <= ABSOLUTE_MAX_ROWS


async def test_listing_is_newest_first(
    repository: SqlAlchemyResearchRepository, session: AsyncSession, owner: uuid.UUID
):
    """`created_at` is a server default, so a client cannot choose it.

    That is the correct behaviour - a caller must not be able to backdate a run
    - which is why this test moves the timestamp with SQL rather than passing
    one to the repository and assuming it was honoured. Passing one would leave
    both rows on the same instant, and the assertion would pass or fail on the
    id tiebreak at random.
    """
    older = await repository.add(make_run(owner))
    newer = await repository.add(make_run(owner))
    await session.execute(
        sa.text(
            "UPDATE research_runs SET created_at = now() - interval '5 minutes' WHERE id = :run_id"
        ),
        {"run_id": older.id},
    )
    await session.flush()

    page, _ = await repository.list_for_user(owner, limit=10)

    assert [run.id for run in page] == [newer.id, older.id]


async def test_a_client_cannot_backdate_a_run(
    repository: SqlAlchemyResearchRepository, owner: uuid.UUID
):
    """The database stamps `created_at`; a value supplied by a caller is ignored."""
    long_ago = dt.datetime(2020, 1, 1, tzinfo=dt.UTC)

    stored = await repository.add(make_run(owner, created_at=long_ago))

    assert stored.created_at.year >= 2026


# --- filters and counts ---------------------------------------------------


async def test_filtering_by_status(repository: SqlAlchemyResearchRepository, owner: uuid.UUID):
    await repository.add(make_run(owner))
    await repository.add(make_run(owner, status=RunStatus.COMPLETED))

    completed, _ = await repository.list_for_user(owner, limit=10, status=RunStatus.COMPLETED)

    assert len(completed) == 1
    assert completed[0].status is RunStatus.COMPLETED


async def test_search_matches_title_or_question_case_insensitively(
    repository: SqlAlchemyResearchRepository, owner: uuid.UUID
):
    await repository.add(make_run(owner, title="Vector databases", question="Compare recall."))
    await repository.add(make_run(owner, title="Inference", question="Compare PRICING models."))

    by_title, _ = await repository.list_for_user(owner, limit=10, query="vector")
    by_question, _ = await repository.list_for_user(owner, limit=10, query="pricing")

    assert len(by_title) == 1
    assert len(by_question) == 1
    assert by_title[0].id != by_question[0].id


async def test_count_active_excludes_finished_runs(
    repository: SqlAlchemyResearchRepository, owner: uuid.UUID
):
    """Feeds the concurrency limit; counting a finished run would lock a user
    out of their own account."""
    await repository.add(make_run(owner, status=RunStatus.QUEUED))
    await repository.add(make_run(owner, status=RunStatus.RESEARCHING))
    await repository.add(make_run(owner, status=RunStatus.COMPLETED))
    await repository.add(make_run(owner, status=RunStatus.FAILED))
    await repository.add(make_run(owner, status=RunStatus.CANCELLED))

    assert await repository.count_active(owner) == 2


async def test_aggregates_are_bounded(repository: SqlAlchemyResearchRepository, owner: uuid.UUID):
    """`all_for_user` is capped so one long history cannot become a table scan."""
    for _ in range(5):
        await repository.add(make_run(owner))

    runs = await repository.all_for_user(owner)

    assert len(runs) == 5
    assert len(runs) <= ABSOLUTE_MAX_ROWS


async def test_has_report_reflects_the_database(
    repository: SqlAlchemyResearchRepository, session: AsyncSession, owner: uuid.UUID
):
    run = await repository.add(make_run(owner, status=RunStatus.COMPLETED))
    assert (await repository.get(run.id, user_id=owner)).has_report is False  # type: ignore[union-attr]

    await session.execute(
        sa.text(
            "INSERT INTO reports (run_id, title, status, model, generated_at) "
            "VALUES (:run_id, 'r', 'validated', 'm', now())"
        ),
        {"run_id": run.id},
    )
    await session.flush()

    assert (await repository.get(run.id, user_id=owner)).has_report is True  # type: ignore[union-attr]
