"""Size the embedding column for the declared embedding model: 1536 to 768.

0002 sized the column for OpenAI's text-embedding-3-small. The model the registry
actually declares - and the only one this repository can price - is Ollama's
nomic-embed-text, at 768 dimensions (Phase 5). pgvector refuses a 768-wide
vector in a 1536-wide column, so until this revision no embedding could have
been stored at all. Found by Phase 7, the first code to write one.

Existing vectors are cleared rather than cast. A vector from a model of another
width is not comparable with the new ones, so keeping it would put rows in the
index that no query can meaningfully match. Embeddings are derived data: the
ingestion pipeline re-embeds any chunk whose ``embedding_model`` is null.

On the ``vector`` line (see 0003): it needs the extension, and nothing on the
relational line may depend on it.

Revision ID: 0004_embedding_dimensions
Revises: 0002_pgvector_embeddings
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_embedding_dimensions"
down_revision: str | None = "0002_pgvector_embeddings"
branch_labels: str | Sequence[str] | None = ("vector",)
depends_on: str | Sequence[str] | None = None

#: Must match app.db.models.source.EMBEDDING_DIMENSIONS.
EMBEDDING_DIMENSIONS = 768
#: What 0002 created, restored on downgrade.
PREVIOUS_DIMENSIONS = 1536


def upgrade() -> None:
    _resize(EMBEDDING_DIMENSIONS)


def downgrade() -> None:
    _resize(PREVIOUS_DIMENSIONS)


def _resize(dimensions: int) -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")
    op.execute(
        "UPDATE document_chunks SET embedding = NULL, embedding_model = NULL "
        "WHERE embedding IS NOT NULL"
    )
    op.execute(
        # An integer constant from this file, never input.
        f"ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector({int(dimensions)})"
    )
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding_hnsw "
        "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
    )
