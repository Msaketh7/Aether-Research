"""Research projects, runs and planner subtasks.

``research_runs`` is the centre of the schema: one row per time a user pressed
"Start Research" (or asked a follow-up). It denormalises ``user_id`` even though
it could be reached through the project, because every authorisation check in
the system filters on it and that filter must never require a join.
"""

from __future__ import annotations

import datetime as dt
import uuid
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import ResearchMode, RunStatus, TaskPriority, TaskStatus
from app.db.base import Base, TimestampMixin, UpdatedAtMixin, fk_uuid, uuid_pk

if TYPE_CHECKING:
    from app.db.models.event import ResearchEventRow
    from app.db.models.evidence import ClaimRow, ContradictionRow
    from app.db.models.report import ReportRow
    from app.db.models.source import SourceRow
    from app.db.models.trace import AgentRunRow
    from app.db.models.user import UserRow


def enum_check(column: str, enum: type[StrEnum], name: str) -> CheckConstraint:
    """Constrain a text column to an enum's values.

    A check constraint rather than a Postgres ENUM type: adding a value to an
    ENUM requires a migration that cannot run inside a transaction on older
    servers, whereas this is an ordinary ``ALTER TABLE``.
    """
    values = ", ".join(f"'{member.value}'" for member in enum)
    return CheckConstraint(f"{column} IN ({values})", name=name)


class ResearchProjectRow(Base, TimestampMixin, UpdatedAtMixin):
    """A folder for related runs. One is created implicitly per run for now."""

    __tablename__ = "research_projects"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = fk_uuid(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    archived_at: Mapped[dt.datetime | None]

    runs: Mapped[list[ResearchRunRow]] = relationship(back_populates="project")


class ResearchRunRow(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "research_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID | None] = fk_uuid(
        ForeignKey("research_projects.id", ondelete="SET NULL")
    )
    user_id: Mapped[uuid.UUID] = fk_uuid(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    #: Set for conversational follow-ups (PRD 5.3). Self-referential.
    parent_run_id: Mapped[uuid.UUID | None] = fk_uuid(
        ForeignKey("research_runs.id", ondelete="SET NULL")
    )

    title: Mapped[str] = mapped_column(Text, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("3"))
    domains: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    date_range_start: Mapped[dt.date | None] = mapped_column(Date)
    date_range_end: Mapped[dt.date | None] = mapped_column(Date)

    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    #: 0..1, written by the worker as it advances. Stored rather than derived
    #: from task completion: deriving it would put a join on every row of the
    #: history list, which is the one query that must stay cheap.
    progress: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False, server_default=text("0"))

    #: Ties the run to its saved LangGraph state so it can resume after a
    #: worker restart (Phase 13). The thread is written when a worker claims the
    #: run; the checkpoint id stays NULL because a resume reads the thread's
    #: latest checkpoint and never names one (``app.agents.runtime``).
    langgraph_thread_id: Mapped[str | None] = mapped_column(Text)
    langgraph_checkpoint_id: Mapped[str | None] = mapped_column(Text)

    # --- the worker's lease on this run (Phase 13) ------------------------
    #: Times a worker has claimed this run. Bounds retries, and counts a worker
    #: that died mid-run, so a run that crashes its process cannot do it
    #: forever. A worker that hands the run back on shutdown gives its attempt
    #: back, because that is not a failed attempt.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    #: Which worker holds the run. Every lifecycle write names it in its WHERE
    #: clause, so a worker whose lease expired cannot overwrite its successor.
    worker_id: Mapped[str | None] = mapped_column(String(64))
    #: Last sign of life from that worker, written at every node boundary. NULL
    #: when nobody holds the run.
    heartbeat_at: Mapped[dt.datetime | None]
    #: When a paused run becomes eligible again. NULL means immediately.
    next_attempt_at: Mapped[dt.datetime | None]

    iteration_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    # Numeric, never float: money that drifts by a rounding error is a bug that
    # only shows up in the monthly bill.
    total_cost_usd: Mapped[float] = mapped_column(
        Numeric(10, 4), nullable=False, server_default=text("0")
    )
    total_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    claim_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    contradiction_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    #: Set when a hard limit (FR-8) truncated the run. Surfaced in the report.
    coverage_caveat: Mapped[str | None] = mapped_column(Text)
    #: The ceilings in force for this run, frozen at creation so a later config
    #: change cannot retroactively rewrite what a finished run was allowed.
    limits: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    started_at: Mapped[dt.datetime | None]
    completed_at: Mapped[dt.datetime | None]
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    user: Mapped[UserRow] = relationship(back_populates="runs")
    project: Mapped[ResearchProjectRow | None] = relationship(back_populates="runs")
    tasks: Mapped[list[ResearchTaskRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    sources: Mapped[list[SourceRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    claims: Mapped[list[ClaimRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    contradictions: Mapped[list[ContradictionRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    agent_runs: Mapped[list[AgentRunRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    report: Mapped[ReportRow | None] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )
    events: Mapped[list[ResearchEventRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        enum_check("mode", ResearchMode, "research_runs_mode"),
        enum_check("status", RunStatus, "research_runs_status"),
        CheckConstraint("depth BETWEEN 1 AND 5", name="ck_research_runs_depth"),
        CheckConstraint("progress BETWEEN 0 AND 1", name="ck_research_runs_progress_range"),
        # The history query: this user's runs, newest first. Sorting by id as a
        # tiebreak makes the order total, which is what keeps cursor pagination
        # from skipping or repeating a row when two runs share a timestamp.
        Index("ix_research_runs_user_id_created_at_id", "user_id", text("created_at DESC"), "id"),
        # The reconciliation sweep: runs stuck in a non-terminal state.
        Index("ix_research_runs_status_created_at", "status", "created_at"),
    )


class ResearchTaskRow(Base, TimestampMixin):
    """A planner subtask (FR-3)."""

    __tablename__ = "research_tasks"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )
    #: Planner-assigned slug such as `market` or `competitors`.
    external_id: Mapped[str] = mapped_column(String(80), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    #: Which loop iteration created it; 2+ means the critic asked for more.
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    claim_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    completed_at: Mapped[dt.datetime | None]

    run: Mapped[ResearchRunRow] = relationship(back_populates="tasks")
    claims: Mapped[list[ClaimRow]] = relationship(back_populates="task")

    __table_args__ = (
        enum_check("priority", TaskPriority, "research_tasks_priority"),
        enum_check("status", TaskStatus, "research_tasks_status"),
        # A planner must not emit the same slug twice within one iteration.
        Index(
            "uq_research_tasks_run_id_external_id_iteration",
            "run_id",
            "external_id",
            "iteration",
            unique=True,
        ),
    )
