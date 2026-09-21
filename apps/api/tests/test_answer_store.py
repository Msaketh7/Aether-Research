"""The answer as a row: projected, rewritten, and read back scoped to its owner.

Real database, because what is under test is what the schema and the projection
order actually do. The point the table exists to make is checked here: an answer
survives a run that never produces a report, which is the whole reason it is not
a column on ``reports``.
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa

from app.agents.schemas import AnswerDraft
from app.answers.repository import answer_identity
from app.db.repositories.answers import SqlAlchemyAnswerRepository
from app.db.session import Database
from app.research.recorder import RunRecorder
from tests.support import agents as fake
from tests.support.projection import WIRE, count, seed_run, seed_source, seed_user

pytestmark = pytest.mark.anyio


@pytest.fixture
async def owner(database: Database) -> uuid.UUID:
    return await seed_user(database)


@pytest.fixture
async def run_id(database: Database, owner: uuid.UUID) -> uuid.UUID:
    return await seed_run(database, owner)


@pytest.fixture
async def recorder(database: Database) -> RunRecorder:
    return RunRecorder(database)


def answer(text: str = "Prices differ [1].", **overrides: object) -> AnswerDraft:
    values: dict[str, object] = {
        "text": text,
        "model": "test-answerer-v1",
        "claim_ids": (),
        "truncated": False,
    }
    values.update(overrides)
    return AnswerDraft.model_validate(values)


async def a_state(database: Database, run_id: uuid.UUID, draft: AnswerDraft | None):
    """A run with one source, one span and one claim, plus the given answer."""
    source_id, document_id = await seed_source(database, run_id, "a", excerpt=WIRE)
    span = fake.evidence("ev-1").model_copy(
        update={"source_id": source_id, "document_id": document_id}
    )
    claim = fake.claim("claim-1", evidence_ids=(span.id,))
    return fake.state(
        research_id=run_id,
        evidence=[span],
        claims=[claim],
        sources=[fake.source("source-a").model_copy(update={"source_id": source_id})],
        answer=draft,
    )


async def read(database: Database, run_id: uuid.UUID, *, user_id: uuid.UUID):
    async with database.session() as session:
        return await SqlAlchemyAnswerRepository(session).answer_for(run_id, user_id=user_id)


async def test_an_answer_is_stored_even_though_the_run_produced_no_report(
    database: Database, recorder: RunRecorder, run_id: uuid.UUID, owner: uuid.UUID
) -> None:
    """The reason this is its own table rather than a column on ``reports``.

    This state has no draft and no citation check, so the report projection
    stores nothing. The answer is still there, which is what makes streaming it
    early worth anything: a run that dies at synthesis has still answered.
    """
    state = await a_state(database, run_id, answer("Capacity is the differentiator [2]."))

    recorded = await recorder.record(state)

    assert recorded.sections is None
    assert await count(database, "reports") == 0
    stored = await read(database, run_id, user_id=owner)
    assert stored is not None
    assert stored.content_md == "Capacity is the differentiator [2]."
    assert stored.model == "test-answerer-v1"
    assert stored.word_count == 5


async def test_a_run_that_wrote_no_answer_stores_no_row(
    database: Database, recorder: RunRecorder, run_id: uuid.UUID, owner: uuid.UUID
) -> None:
    """Absent, not empty. An empty answer would render as one the run gave."""
    await recorder.record(await a_state(database, run_id, None))

    assert await count(database, "run_answers") == 0
    assert await read(database, run_id, user_id=owner) is None


async def test_re_recording_a_run_rewrites_its_answer_rather_than_adding_one(
    database: Database, recorder: RunRecorder, run_id: uuid.UUID, owner: uuid.UUID
) -> None:
    """A resumed run projects again, and a run answers its question once.

    The id is derived from the run, so this is an upsert on a stable key rather
    than a second answer to the same question with no way to say which is
    current - and the reader's screen was told to start again for exactly this.
    """
    await recorder.record(await a_state(database, run_id, answer("A first attempt [1].")))
    await recorder.record(await a_state(database, run_id, answer("A different second one [1].")))

    assert await count(database, "run_answers") == 1
    stored = await read(database, run_id, user_id=owner)
    assert stored is not None
    assert stored.content_md == "A different second one [1]."
    assert stored.id == answer_identity(run_id)


async def test_a_truncated_answer_says_so_when_it_is_read_back(
    database: Database, recorder: RunRecorder, run_id: uuid.UUID, owner: uuid.UUID
) -> None:
    """A reader is entitled to know the answer stops early rather than ends."""
    await recorder.record(
        await a_state(database, run_id, answer("It stops mid-sen", truncated=True))
    )

    stored = await read(database, run_id, user_id=owner)
    assert stored is not None and stored.truncated


async def test_another_user_cannot_read_this_run_s_answer(
    database: Database, recorder: RunRecorder, run_id: uuid.UUID
) -> None:
    """Scoped through ``research_runs`` like every other read in this package."""
    await recorder.record(await a_state(database, run_id, answer()))
    stranger = await seed_user(database)

    assert await read(database, run_id, user_id=stranger) is None


async def test_deleting_a_run_takes_its_answer_with_it(
    database: Database, recorder: RunRecorder, run_id: uuid.UUID
) -> None:
    """``ON DELETE CASCADE``, so an answer cannot outlive the question."""
    await recorder.record(await a_state(database, run_id, answer()))

    async with database.session() as session:
        await session.execute(sa.text("DELETE FROM research_runs WHERE id = :id"), {"id": run_id})
        await session.commit()

    assert await count(database, "run_answers") == 0
