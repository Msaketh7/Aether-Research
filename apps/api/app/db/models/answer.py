"""The direct answer a run produced, as a row.

Its own table rather than a column on ``reports``, for one reason that decides
it: the answer is written *before* the report, and it is the artifact the reader
came for. Hanging it off the report would mean a run that answered the question
and then died at synthesis had nothing to show - which is exactly the failure
streaming the answer early exists to prevent.

It is a read model, not a record of anything the report does not already hold:
every character in ``content_md`` was streamed as ``answer_delta`` events, which
are themselves durable (``research_events``). This is what serves a reader who
was not watching, in one query instead of a replay.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UpdatedAtMixin, fk_uuid, uuid_pk

if TYPE_CHECKING:
    from app.db.models.research import ResearchRunRow


class RunAnswerRow(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "run_answers"

    id: Mapped[uuid.UUID] = uuid_pk()
    #: Unique: a run answers its question once. A second row would be two
    #: answers to one question with no way to say which is current.
    run_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False, unique=True
    )

    #: Markdown with inline ``[n]`` markers, numbered against the report's
    #: citations when there is a report. A run that fails before synthesis keeps
    #: its markers unresolved rather than losing them: an unresolved marker is
    #: visible to the reader, a scrubbed one is not.
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    #: The model that answered, as the provider reported it. Not the role's
    #: configured model: failover means the second in the chain may have written
    #: it, and a quality question has to name the one that did.
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    #: How many distinct claims the prose actually cites. Counted from the
    #: markers that resolved, so it is a measurement rather than an intention.
    citation_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    #: The model hit its output ceiling mid-answer. Stored rather than hidden: a
    #: reader is entitled to know the answer stops early.
    truncated: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    generated_at: Mapped[dt.datetime] = mapped_column(nullable=False)

    run: Mapped[ResearchRunRow] = relationship(back_populates="answer")

    __table_args__ = (
        # Unprefixed, like every ``enum_check`` name: the metadata's convention
        # adds the ``ck_<table>_`` itself, and a name that already carries one
        # comes out doubled in the database.
        CheckConstraint("word_count >= 0", name="run_answers_word_count"),
        CheckConstraint("citation_count >= 0", name="run_answers_citation_count"),
    )
