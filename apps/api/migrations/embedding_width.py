"""Resizing the embedding column, as one operation rather than five.

Changing embedding model family changes the width of every vector, and pgvector
indexes a declared width - so it is a migration and a re-embed, not a
configuration flip (`app/db/models/source.py`). That migration is the same five
statements every time, and getting any of them wrong is quiet:

  * leave the HNSW index in place and the resize is at best relying on the
    server to rebuild an index whose operator class was chosen for vectors of
    the old width - 0004 dropped it first and this does too;
  * keep the old vectors and the index holds rows no query can match, because
    cosine distance between vectors from two models is a number and not a
    meaningful one;
  * forget to rebuild the index and every search falls back to a sequential
    scan, which is slower and still returns answers - so nothing tells you.

So it lives here, called by the revision that performs it. 0004 did this inline
and is left as it was: a migration that has run somewhere is history, not code
to refactor.

## Changing the embedding model

1. Declare the new model in the registry (or in the file `MODEL_REGISTRY_PATH`
   names) and point `EMBEDDING_MODEL` at its key.
2. Set `EMBEDDING_DIMENSIONS` in `app/db/models/source.py` to the width that
   model emits.
3. Add a revision on the `vector` branch that calls `resize` with the same
   number, and `alembic upgrade heads`.
4. Re-embed. Chunks come back with `embedding_model` null, and the ingestion
   pipeline treats a null as "not embedded yet".

Steps 2 and 3 are two halves of one change, and
`tests/test_schema.py::test_the_embedding_column_matches_the_declared_width`
fails the build when only one of them lands.
"""

from __future__ import annotations

from alembic import op

#: The HNSW index over the column. Named here because three statements need it
#: and a typo in one of them is a silent sequential scan.
INDEX_NAME = "ix_document_chunks_embedding_hnsw"


def statements(dimensions: int) -> list[str]:
    """The four statements a resize is, in the order they have to run.

    Separate from executing them so the order is testable without a migration
    context and without pgvector - which matters here, because three of the four
    ways to get this wrong produce no error at all, and the one machine this was
    written on cannot run the extension.
    """
    if dimensions < 1:
        raise ValueError(f"embedding width must be positive, not {dimensions}")

    return [
        # Before the ALTER, as 0004 did it: the index is over vectors of a
        # width that is about to stop existing, and it is rebuilt below rather
        # than left to whatever the server would do with it.
        f"DROP INDEX IF EXISTS {INDEX_NAME}",
        # Before the ALTER too. Clearing after it would mean asking pgvector to
        # cast vectors it has already refused.
        "UPDATE document_chunks SET embedding = NULL, embedding_model = NULL "
        "WHERE embedding IS NOT NULL",
        # An integer from the caller, forced through int() and interpolated
        # because a type modifier cannot be a bind parameter.
        f"ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector({int(dimensions)})",
        # Rebuilt, not left off. A missing HNSW index is the failure with no
        # symptom: every search still returns the right rows, by sequential scan.
        f"CREATE INDEX {INDEX_NAME} ON document_chunks USING hnsw (embedding vector_cosine_ops)",
    ]


def resize(dimensions: int) -> None:
    """Take ``document_chunks.embedding`` to ``dimensions``, clearing vectors.

    Existing vectors are cleared rather than cast. A vector from a model of
    another width is not comparable with the new ones, and embeddings are
    derived data: whatever is dropped here is recomputed by re-ingesting.
    """
    for statement in statements(dimensions):
        op.execute(statement)
