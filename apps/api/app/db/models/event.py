"""The progress stream, persisted (ADR 0006).

"Every streamed event is also persisted, so the activity trace is
reconstructible without the stream" is the part of ADR 0006 that only becomes
true here. Two things rest on it:

* **Replay outlives the buffer.** A browser reconnecting with ``Last-Event-ID``
  is served from these rows, so a run that streamed for an hour, or a Redis
  that was restarted mid-run, still replays exactly. The Redis buffer stays as
  the fast path for the common case and is allowed to be lossy, because it is
  no longer the only copy.
* **The sequence has one owner.** ``(run_id, seq)`` is unique, and the number is
  allocated by the insert that stores the event. A worker and an API process
  emitting for the same run cannot produce two events with the same ``id:``,
  which is the failure a per-process counter would have shipped.

The payload is whatever the event type declares in ``@aether/shared-types``. It
is bounded by its producer rather than by a column: every payload here is
counts, ids and text the system itself wrote, never a fetched page.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ResearchEventType, RunStatus
from app.db.base import Base, TimestampMixin, fk_uuid, uuid_pk
from app.db.models.research import enum_check

if TYPE_CHECKING:
    from app.db.models.research import ResearchRunRow


class ResearchEventRow(Base, TimestampMixin):
    """One progress event, exactly as it was streamed."""

    __tablename__ = "research_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )
    #: Monotonic per run, allocated by the insert. This is the SSE ``id:``.
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The run's status when the event was emitted, so a replayed stream moves
    #: the header exactly as the live one did.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: When the event happened, which is not when the row was written: a retry
    #: of the insert must not move an event forward in time.
    occurred_at: Mapped[dt.datetime] = mapped_column(nullable=False)

    run: Mapped[ResearchRunRow] = relationship(back_populates="events")

    __table_args__ = (
        # The uniqueness that makes the sequence trustworthy, and the index
        # every replay reads: "this run's events after n", in order.
        UniqueConstraint("run_id", "seq", name="uq_research_events_run_id_seq"),
        enum_check("type", ResearchEventType, "research_events_type"),
        enum_check("status", RunStatus, "research_events_status"),
        Index("ix_research_events_run_id_seq", "run_id", "seq"),
    )
