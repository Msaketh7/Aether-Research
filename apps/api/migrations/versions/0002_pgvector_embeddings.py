"""Enable pgvector and add the chunk embedding column.

Separate from the core schema because the extension is a server-side
prerequisite: a managed Postgres may require it to be enabled by an operator
before any role can create it. Isolating it means a missing extension produces
one actionable failure here rather than aborting the entire schema.

Nothing writes to this column until Phase 7 (document ingestion); it exists now
so the schema is complete and the retrieval work of Phase 8 has somewhere to
read from.

Revision ID: 0002_pgvector_embeddings
Revises: 0001_core_schema
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0002_pgvector_embeddings"
down_revision: str | None = "0001_core_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Must match app.db.models.source.EMBEDDING_DIMENSIONS. pgvector indexes a
#: declared width, so this is schema, not configuration.
EMBEDDING_DIMENSIONS = 1536


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column(
        "document_chunks",
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=True),
    )

    # HNSW over cosine distance: the retrieval layer normalises embeddings and
    # compares by angle, so cosine is the operator class that matches how the
    # vectors are actually used. HNSW rather than IVFFlat because it needs no
    # training pass and stays correct as rows are added one at a time, which is
    # exactly how ingestion writes them.
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding_hnsw "
        "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    """Drops the column and its index.

    The extension is left in place: it may predate this schema or be in use
    elsewhere in the database.
    """
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")
    op.drop_column("document_chunks", "embedding")
