"""Postgres storage for a run's answer.

Implements ``AnswerStore``: what the projection writes after a run, and the read
behind ``GET /research/{id}/answer``.

The write is an upsert on a derived id, which makes re-projecting a run a
rewrite rather than a second answer. The read joins ``research_runs`` for
``user_id``, like every other read in this package: an answer belongs to a run,
and the run belongs to a person.
"""

from __future__ import annotations

import uuid
from typing import cast

from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.answers.repository import AnswerRecord
from app.answers.schemas import RunAnswer
from app.db.models.answer import RunAnswerRow
from app.db.models.research import ResearchRunRow

_ANSWERS = cast(Table, RunAnswerRow.__table__)


class SqlAlchemyAnswerRepository:
    """Reads and writes one run's answer."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_answer(self, answer: AnswerRecord) -> None:
        statement = insert(_ANSWERS).values(
            {
                "id": answer.id,
                "run_id": answer.run_id,
                "content_md": answer.content_md,
                "model": answer.model,
                "word_count": answer.word_count,
                "citation_count": answer.citation_count,
                "truncated": answer.truncated,
                "generated_at": answer.generated_at,
            }
        )
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[_ANSWERS.c.id],
                # Everything a re-answer can change. `run_id` is what the id is
                # derived from, so it cannot.
                set_={
                    "content_md": statement.excluded.content_md,
                    "model": statement.excluded.model,
                    "word_count": statement.excluded.word_count,
                    "citation_count": statement.excluded.citation_count,
                    "truncated": statement.excluded.truncated,
                    "generated_at": statement.excluded.generated_at,
                },
            )
        )

    async def answer_for(self, run_id: uuid.UUID, *, user_id: uuid.UUID) -> RunAnswer | None:
        statement = (
            select(RunAnswerRow)
            .join(ResearchRunRow, ResearchRunRow.id == RunAnswerRow.run_id)
            .where(RunAnswerRow.run_id == run_id, ResearchRunRow.user_id == user_id)
        )
        row = (await self._session.execute(statement)).scalar_one_or_none()
        if row is None:
            return None
        return RunAnswer(
            id=row.id,
            run_id=row.run_id,
            content_md=row.content_md,
            model=row.model,
            word_count=row.word_count,
            citation_count=row.citation_count,
            truncated=row.truncated,
            generated_at=row.generated_at,
        )
