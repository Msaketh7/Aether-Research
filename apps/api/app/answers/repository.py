"""Persistence boundary for a run's answer.

The protocol lives here, next to the domain it serves; the Postgres
implementation lives in ``app/db/repositories/answers.py``. Same arrangement as
``app.reports.repository``, and the same two rules: the write is idempotent on a
derived id, and the read is scoped by ``user_id`` through ``research_runs``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Protocol

from app.answers.schemas import RunAnswer

#: The answer's id is derived from the run, because a run answers once. A
#: re-projection therefore rewrites the row rather than adding a second answer
#: to the same question (ADR 0016).
_ANSWER_NAMESPACE = uuid.UUID("5d0a91c6-3f74-4e28-8b15-7c9e4a2d60f3")


def answer_identity(run_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(_ANSWER_NAMESPACE, str(run_id))


@dataclass(frozen=True, slots=True)
class AnswerRecord:
    id: uuid.UUID
    run_id: uuid.UUID
    content_md: str
    model: str
    word_count: int
    citation_count: int
    truncated: bool
    generated_at: dt.datetime


class AnswerStore(Protocol):
    """What the answer projection writes and what the research service reads."""

    async def record_answer(self, answer: AnswerRecord) -> None:
        """Store this run's answer, replacing one already there.

        Replacing matters on exactly one path: a worker that crashed mid-answer
        is resumed, writes a *different* answer, and the reader has already been
        told to start again (``answer_started``). The row must agree with what
        they are looking at.
        """
        ...

    async def answer_for(self, run_id: uuid.UUID, *, user_id: uuid.UUID) -> RunAnswer | None:
        """The stored answer, or ``None`` when the run has not written one."""
        ...
