"""Give a run the four facts a worker needs to hold it, hand it back, or retry it.

Until now a run's row said what it was doing but nothing about *who* was doing
it. That is the gap Phase 13 closes, and every one of these columns exists
because some failure is otherwise unrecoverable:

``worker_id`` and ``heartbeat_at`` are the lease. A worker writes both when it
claims a run and renews the heartbeat at every node boundary; every later write
it makes names its own id in the WHERE clause. Without them a worker that is
killed leaves a run in ``researching`` forever, because nothing can tell a run
that is progressing from a run whose process is gone.

``attempts`` bounds retries. It is counted when a run is *claimed*, not when one
fails, so a run that crashes its worker spends an attempt too - otherwise a
document or a question that kills the process would be redelivered until the end
of time. A worker that hands a run back on shutdown returns the attempt, because
a graceful stop is not a failed attempt.

``next_attempt_at`` is the backoff, held here rather than in a delayed queue. A
retry Redis forgets is a run that never resumes, and the reconciliation sweep
this phase adds already reads Postgres (ADR 0005).

No run has ever been executed - Phase 13 is the first code that runs one - so
the defaults below describe every existing row correctly: nobody holds it, it
has been claimed zero times, and it is due now.

Revision ID: 0009_worker_lease
Revises: 0008_report_validation
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_worker_lease"
down_revision: str | None = "0008_report_validation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_runs",
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("research_runs", sa.Column("worker_id", sa.String(length=64), nullable=True))
    op.add_column(
        "research_runs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "research_runs", sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("research_runs", "next_attempt_at")
    op.drop_column("research_runs", "heartbeat_at")
    op.drop_column("research_runs", "worker_id")
    op.drop_column("research_runs", "attempts")
