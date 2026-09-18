"""Let the ledger say a call's price was never declared.

``llm_calls.cost_usd`` and ``agent_runs.cost_usd`` were `NOT NULL DEFAULT 0`.
Phase 16 is the first code to write them, and the first thing it found is that
the column cannot express what the rest of the system is careful to preserve: a
model the registry does not price produces *no* cost, which is not a cost of
zero. Recorded as `0`, an unpriced run reads as a free one, the per-run total
reads as measured, and the caveat the graph attaches ("a model call could not
be costed, so this run's spending limit could not be enforced") contradicts the
ledger beside it.

So both become nullable, and null means *not measured*. ``tool_calls.cost_usd``
is left alone: a tool call that costs nothing genuinely costs nothing, and
every one of the six is free at the point of use.

No row has ever been written to either table, so there is nothing to migrate
and the downgrade's `NOT NULL` cannot fail on existing data. It would fail on
data written *after* this revision, which is the correct behaviour: the
information is real and a downgrade would have to decide what to do with it.

Revision ID: 0011_uncosted_calls
Revises: 0010_research_events
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_uncosted_calls"
down_revision: str | None = "0010_research_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (("llm_calls", "cost_usd"), ("agent_runs", "cost_usd"))


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.Numeric(10, 4),
            nullable=True,
            existing_server_default=sa.text("0"),
            server_default=None,
        )


def downgrade() -> None:
    for table, column in _COLUMNS:
        op.execute(sa.text(f"UPDATE {table} SET {column} = 0 WHERE {column} IS NULL"))  # noqa: S608
        op.alter_column(
            table,
            column,
            existing_type=sa.Numeric(10, 4),
            nullable=False,
            server_default=sa.text("0"),
        )
