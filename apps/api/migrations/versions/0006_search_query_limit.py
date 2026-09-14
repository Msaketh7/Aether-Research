"""Freeze the search-query ceiling with the rest of a run's limits.

``research_runs.limits`` holds the FR-8 ceilings in force for a run, frozen when
it is created so that a later configuration change cannot rewrite what the run
was allowed. Four of the five ceilings the research graph enforces were frozen
there; ``max_search_queries`` was read from configuration only, so it was the one
limit a deployment could change underneath a queued run.

``RunLimits`` now carries it. Runs created before this revision are backfilled
with 30, the documented default and the value every such run was created under
unless the deployment had overridden ``MAX_SEARCH_QUERIES`` - which was not
recorded, and cannot be recovered. No run had executed when this revision was
written (the worker is Phase 13), so no finished run's history is rewritten.

Only rows that already carry frozen limits are touched: a row written without
any (test fixtures seed some) has nothing to extend.

Revision ID: 0006_search_query_limit
Revises: 0005_graph_checkpoints
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_search_query_limit"
down_revision: str | None = "0005_graph_checkpoints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Settings.max_search_queries's default when this revision was written.
DEFAULT_MAX_SEARCH_QUERIES = 30


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE research_runs "
            "SET limits = limits || jsonb_build_object('max_search_queries', CAST(:value AS integer)) "
            "WHERE limits -> 'max_iterations' IS NOT NULL "
            "AND limits -> 'max_search_queries' IS NULL"
        ).bindparams(value=DEFAULT_MAX_SEARCH_QUERIES)
    )


def downgrade() -> None:
    op.execute(
        "UPDATE research_runs SET limits = limits - 'max_search_queries' "
        "WHERE limits -> 'max_search_queries' IS NOT NULL"
    )
