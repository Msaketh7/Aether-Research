"""Let a report say what its citation check found, and what it could not compute.

``reports.overall_confidence`` is the mean confidence of the claims a report
cites. A report that cites none - every marker its writer produced was rejected
by the citation validator, and its repair budget ran out - has no such mean, and
the column's ``NOT NULL DEFAULT 0.50`` would have shown the reader a placeholder
in the one case where the number matters most.

Same change, and the same reason, as ``sources.relevance_score`` in 0007: a score
a person reads has to be able to say "not computed".

``reports.validation`` is new, and is the other half of the same idea. The
citation validator's verdict - how many markers were checked, how many resolved,
and why the rest did not - is shown above the prose so a reader can decide how
far to trust it. It lived only in the graph's checkpoint, which no request can
read, and only the counts are derivable from the stored text: the *reasons* are
not. NULL means the check never ran.

No report row has ever been written (Phase 12 is the first code that writes one),
so the backfill below touches nothing. It is here because a migration that is
correct only on an empty table is a migration that fails the first time it runs
anywhere else.

Revision ID: 0008_report_validation
Revises: 0007_evidence_read_model
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_report_validation"
down_revision: str | None = "0007_evidence_read_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "reports",
        "overall_confidence",
        existing_type=sa.Numeric(3, 2),
        nullable=True,
        server_default=None,
    )
    op.add_column(
        "reports",
        sa.Column("validation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reports", "validation")
    # The placeholder is what the column meant before, so restoring it restores
    # the old behaviour exactly - including its dishonesty about rows that never
    # had a measurement.
    op.execute("UPDATE reports SET overall_confidence = 0.50 WHERE overall_confidence IS NULL")
    op.alter_column(
        "reports",
        "overall_confidence",
        existing_type=sa.Numeric(3, 2),
        nullable=False,
        server_default=sa.text("0.50"),
    )
