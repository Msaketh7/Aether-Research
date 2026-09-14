"""LangGraph checkpoint tables, created by Alembic rather than by the library.

The research graph writes a checkpoint after every node (ADR 0002, ADR 0014),
through ``langgraph-checkpoint-postgres``. That library ships its schema as a
list of statements and applies them itself from ``setup()``, recording progress
in its own ``checkpoint_migrations`` table. Letting it do so would give this
database two migration histories, one of them applied by whichever worker
happened to start first.

So this revision applies the same statements and records the same versions.
``setup()`` then finds the schema current and does nothing, and the worker never
calls it: the checkpointer factory checks the recorded version at startup and
refuses to run against a schema older than the installed library expects.

The statements are a frozen copy, not an import. A migration has to do the same
thing on every run for as long as it exists, and importing the list would make
this revision's meaning change whenever the library is upgraded. A test compares
the copy with the installed library, so an upgrade that adds a statement fails
the build and prompts a new revision instead of drifting silently. The copied
DDL is MIT-licensed (see THIRD_PARTY_NOTICES.md).

Three of the statements create indexes ``CONCURRENTLY``, which Postgres refuses
inside a transaction; they run in an autocommit block.

On the ``core`` line: relational, and needs no extension (see 0003).

Revision ID: 0005_graph_checkpoints
Revises: 0003_document_ingestion
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_graph_checkpoints"
down_revision: str | None = "0003_document_ingestion"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ``langgraph.checkpoint.postgres.base.BasePostgresSaver.MIGRATIONS`` from
#: langgraph-checkpoint-postgres 3.1.2, in order. The index in this tuple is the
#: version the library records for each statement.
CHECKPOINT_MIGRATIONS: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v INTEGER PRIMARY KEY
);""",
    """CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);""",
    """CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);""",
    """CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    blob BYTEA NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);""",
    "ALTER TABLE checkpoint_blobs ALTER COLUMN blob DROP not null;",
    # A no-op in the library too: it keeps the recorded versions aligned with an
    # empty migration the library once shipped.
    "SELECT 1;",
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id);
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id);
    """,
    """
    CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id);
    """,
    """ALTER TABLE checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT '';""",
)


def upgrade() -> None:
    record = sa.text("INSERT INTO checkpoint_migrations (v) VALUES (:v)")
    for version, statement in enumerate(CHECKPOINT_MIGRATIONS):
        if "CONCURRENTLY" in statement:
            with op.get_context().autocommit_block():
                op.execute(statement)
        else:
            op.execute(statement)
        op.execute(record.bindparams(v=version))


def downgrade() -> None:
    """Drops the tables, which takes every saved checkpoint with them.

    Checkpoints are resumable progress, not results: a run whose checkpoint is
    gone restarts rather than resumes. Nothing a report cites lives here.
    """
    op.execute(
        "DROP TABLE IF EXISTS checkpoint_writes, checkpoint_blobs, checkpoints, "
        "checkpoint_migrations"
    )
