"""Record when a run was last put on the queue (Phase 22).

The reconciliation sweep asks Postgres which runs *should* be on the queue and
re-enqueues them. Until now the question had no memory: a run that is still
`queued` because every worker slot is busy answers yes on every sweep, and
neither the sweep nor the queue deduplicates - so a backlog that outlives the
grace period grows the queue by up to `worker_sweep_batch` entries every sweep,
for as long as it lasts.

Phase 21's load test measured it: **4,662 queue entries for 100 offered jobs**.
Nothing ran twice, because claiming a run is a conditional update and the
duplicate delivery is dropped. What broke was the measurement - `queue_depth`
is the gauge on the dashboard and the obvious input to a worker autoscaler, and
it was wrong by a factor of forty-six - and, under Redis rather than the
in-memory adapter, the list itself would grow without bound.

`last_queued_at` gives the question its memory, and the rule it enables is one
sentence: **re-dispatch a run only if something has happened to it since we
last queued it, or if that was long enough ago that the dispatch was evidently
lost.** "Something has happened" is `last_queued_at <= updated_at`, which is
what makes a handed-back run, a due retry and an expired lease all dispatch
immediately while a merely-waiting run does not. The stamp deliberately does
*not* touch `updated_at`: putting a run on the queue again is not a change to
the run, and if it counted as one the rule would re-arm itself every sweep.

Nullable with no default, because null means *never dispatched by a sweep*,
which is the truth for every run that exists today and for every run the API
enqueues itself.

Revision ID: 0013_dispatch_clock
Revises: 0012_audit_log
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_dispatch_clock"
down_revision: str | None = "0012_audit_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "research_runs", sa.Column("last_queued_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("research_runs", "last_queued_at")
