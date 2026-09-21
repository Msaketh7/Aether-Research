"""The answer as the API serves it."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.agents.schemas import MAX_ANSWER_CHARS
from app.research.schemas import ApiModel


class RunAnswer(ApiModel):
    """The direct answer to the question the run was given.

    ``content_md`` is Markdown containing inline ``[n]`` markers. They are
    numbered against the report's citations, so a marker resolves through
    ``GET /research/{id}/report``; on a run with no report they resolve to
    nothing and the reader's renderer shows them unresolved, which is the
    honest rendering of a citation whose chain was never validated.
    """

    id: UUID
    run_id: UUID
    content_md: str = Field(max_length=MAX_ANSWER_CHARS)
    model: str
    word_count: int = Field(ge=0)
    citation_count: int = Field(ge=0)
    #: The model reached its output ceiling, so the answer stops early.
    truncated: bool
    generated_at: datetime


class AnswerResponse(ApiModel):
    """``answer`` is ``None`` until the run has written one.

    Null rather than a 404, because "not yet" is the normal state of a run that
    is still researching, and a frontend polling a 404 cannot tell it apart from
    a run that does not exist.
    """

    answer: RunAnswer | None = None
