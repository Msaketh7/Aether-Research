"""Persist the progress stream, so replay does not depend on a cache.

ADR 0006 said every streamed event is also persisted; until Phase 14 nothing
was, because nothing but the API itself emitted. Now the worker does, and two
of that ADR's promises need a table to be true.

``(run_id, seq)`` is unique, and it is the insert that allocates the number -
which is what makes the SSE ``id:`` trustworthy once a worker and an API
process both emit for one run. A per-process counter would hand the same id to
two different events, and a browser reconnecting with ``Last-Event-ID`` would
silently skip one of them.

The composite index is the replay query: this run's events after n, in order.
It also serves the unique constraint, but it is declared for the read.

No run has streamed a persisted event before this revision, so there is nothing
to backfill: the table starts empty and the first worker run fills it.

Revision ID: 0010_research_events
Revises: 0009_worker_lease
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.enums import ResearchEventType, RunStatus

revision: str = "0010_research_events"
down_revision: str | None = "0009_worker_lease"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _in_list(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.create_table(
        "research_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _in_list("type", [member.value for member in ResearchEventType]),
            name="ck_research_events_research_events_type",
        ),
        sa.CheckConstraint(
            _in_list("status", [member.value for member in RunStatus]),
            name="ck_research_events_research_events_status",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            name="fk_research_events_run_id_research_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_research_events"),
        sa.UniqueConstraint("run_id", "seq", name="uq_research_events_run_id_seq"),
    )
    op.create_index("ix_research_events_created_at", "research_events", ["created_at"])
    op.create_index("ix_research_events_run_id", "research_events", ["run_id"])
    op.create_index("ix_research_events_run_id_seq", "research_events", ["run_id", "seq"])


def downgrade() -> None:
    op.drop_index("ix_research_events_run_id_seq", table_name="research_events")
    op.drop_index("ix_research_events_run_id", table_name="research_events")
    op.drop_index("ix_research_events_created_at", table_name="research_events")
    op.drop_table("research_events")
