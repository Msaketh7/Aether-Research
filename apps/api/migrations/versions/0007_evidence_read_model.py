"""Let a source say that its relevance was never measured.

``sources.relevance_score`` has been ``NOT NULL DEFAULT 0.50`` since the schema
was written, and nothing has ever computed it. That default is why Phase 7 left
``GET /research/{id}/sources`` returning an empty list rather than real rows: the
``Source`` DTO would have rendered the placeholder as "relevance 50%", which is a
measurement the system never made.

Phase 11 serves the endpoint, so the column has to be able to hold "unknown".
It becomes nullable with no default, and every existing row - all of which hold
the placeholder rather than a measurement - is set to NULL.

The downgrade cannot restore what was never there, so it restores the default
and writes it into the rows it made NOT NULL. That is lossy in exactly one
direction that matters: a genuine 0.50 measured after this revision comes back
as a 0.50 placeholder, indistinguishable from the rest. No relevance has been
measured at the time of writing, so no such value exists yet.

``sources.dedup_reason`` is new: which rule collapsed a source into its cluster.
The cluster itself needs no column - its id is a UUID5 over the run and its
primary source, so the primary is the member that reproduces the id - but why two
sources were judged the same content is a fact about the judgement that cannot be
recomputed from the row, and it is shown to the reader beside the badge.

``claims.corroboration_count`` gains an index-free check constraint at the same
time: it counts distinct sources behind a claim and a negative one would mean a
bug in the projection rather than data worth keeping.

Revision ID: 0007_evidence_read_model
Revises: 0006_search_query_limit
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_evidence_read_model"
down_revision: str | None = "0006_search_query_limit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "sources",
        "relevance_score",
        existing_type=sa.Numeric(3, 2),
        nullable=True,
        server_default=None,
    )
    op.execute("UPDATE sources SET relevance_score = NULL")
    op.add_column("sources", sa.Column("dedup_reason", sa.String(20), nullable=True))
    op.create_check_constraint(
        "ck_sources_dedup_reason",
        "sources",
        "dedup_reason IS NULL OR dedup_reason IN ('exact_hash', 'canonical_url', 'near_duplicate')",
    )
    op.create_check_constraint(
        "ck_claims_corroboration_non_negative", "claims", "corroboration_count >= 0"
    )


def downgrade() -> None:
    op.drop_constraint("ck_claims_corroboration_non_negative", "claims", type_="check")
    op.drop_constraint("ck_sources_dedup_reason", "sources", type_="check")
    op.drop_column("sources", "dedup_reason")
    op.execute("UPDATE sources SET relevance_score = 0.50 WHERE relevance_score IS NULL")
    op.alter_column(
        "sources",
        "relevance_score",
        existing_type=sa.Numeric(3, 2),
        nullable=False,
        server_default=sa.text("0.50"),
    )
