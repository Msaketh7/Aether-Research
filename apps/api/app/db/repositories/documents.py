"""Postgres storage for the sources, documents and chunks ingestion writes.

Two things here are not ordinary ORM writes, each for a stated reason:

* **Vectors are written with an explicit text-to-vector cast** in a plain SQL
  ``UPDATE``. That needs no pgvector codec registered on the driver, so it
  behaves the same on every connection; and ``embedding_model IS NULL`` in the
  WHERE clause means a repeated or concurrent embedding pass cannot overwrite a
  vector that is already there.
* **Writers of one source are serialised with a transaction-scoped advisory
  lock.** Job delivery is at-least-once (ADR 0005), so one ingestion can run
  twice at the same moment. Without the lock both would find no source, both
  would insert one, and the run would count one upload as two sources.

Bounded like every repository here: each list query carries a LIMIT, and each
read of chunks is scoped to a run the caller owns.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import and_, func, insert, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import DocumentFormat, SourceType
from app.db.models.research import ResearchRunRow
from app.db.models.source import DocumentChunkRow, DocumentRow, SourceRow
from app.retrieval.filters import ChunkFilter, ChunkView
from app.sources.untrusted import UntrustedText

#: Guard on every list query, even when a caller forgets to pass one.
ABSOLUTE_MAX_ROWS = 200

#: Rows per INSERT when writing chunks. A 2,000-chunk document is ten
#: statements rather than one enormous one or two thousand small ones.
_INSERT_BATCH = 200

_STORE_EMBEDDING = text(
    "UPDATE document_chunks "
    "SET embedding = CAST(CAST(:vector AS text) AS vector), embedding_model = :model "
    "WHERE id = :id AND embedding_model IS NULL"
)


class SqlAlchemyDocumentRepository:
    """Reads and writes ingestion's rows for one session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- sources ----------------------------------------------------------

    async def lock(self, key: str) -> None:
        """Serialise every writer that takes the same key, until this transaction ends."""
        await self._session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(key, 0)))
        )

    async def find_source(
        self, *, run_id: uuid.UUID, source_type: SourceType, canonical_url: str
    ) -> SourceRow | None:
        statement = (
            select(SourceRow)
            .where(
                SourceRow.run_id == run_id,
                SourceRow.source_type == source_type.value,
                SourceRow.canonical_url == canonical_url,
            )
            .order_by(SourceRow.created_at, SourceRow.id)
            .limit(1)
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def add_source(self, values: Mapping[str, Any]) -> SourceRow:
        row = SourceRow(**values)
        self._session.add(row)
        await self._session.flush()
        # The count the run page and dashboard show, incremented in the same
        # transaction as the row it counts so the two cannot disagree.
        await self._session.execute(
            update(ResearchRunRow)
            .where(ResearchRunRow.id == row.run_id)
            .values(source_count=ResearchRunRow.source_count + 1)
        )
        return row

    # --- documents --------------------------------------------------------

    async def latest_document(self, source_id: uuid.UUID) -> DocumentRow | None:
        statement = (
            select(DocumentRow)
            .where(DocumentRow.source_id == source_id)
            .order_by(DocumentRow.created_at.desc(), DocumentRow.id.desc())
            .limit(1)
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def document_by_hash(self, source_id: uuid.UUID, content_hash: str) -> DocumentRow | None:
        statement = select(DocumentRow).where(
            DocumentRow.source_id == source_id, DocumentRow.content_hash == content_hash
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def add_document(self, values: Mapping[str, Any]) -> DocumentRow:
        row = DocumentRow(**values)
        self._session.add(row)
        await self._session.flush()
        return row

    # --- chunks -----------------------------------------------------------

    async def add_chunks(self, rows: Sequence[Mapping[str, Any]]) -> None:
        """Insert chunk rows without vectors; ``embed_pending`` fills those in."""
        for offset in range(0, len(rows), _INSERT_BATCH):
            batch = [dict(row) for row in rows[offset : offset + _INSERT_BATCH]]
            await self._session.execute(insert(DocumentChunkRow), batch)

    async def pending_embeddings(
        self, document_id: uuid.UUID, *, limit: int
    ) -> list[tuple[uuid.UUID, str]]:
        """Chunks still without a vector, in document order."""
        statement = (
            select(DocumentChunkRow.id, DocumentChunkRow.content)
            .where(
                DocumentChunkRow.document_id == document_id,
                DocumentChunkRow.embedding_model.is_(None),
            )
            .order_by(DocumentChunkRow.chunk_index)
            .limit(max(1, min(limit, ABSOLUTE_MAX_ROWS)))
        )
        return [(row.id, row.content) for row in await self._session.execute(statement)]

    async def count_pending(self, document_id: uuid.UUID) -> int:
        statement = (
            select(func.count())
            .select_from(DocumentChunkRow)
            .where(
                DocumentChunkRow.document_id == document_id,
                DocumentChunkRow.embedding_model.is_(None),
            )
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def store_embeddings(
        self, vectors: Sequence[tuple[uuid.UUID, Sequence[float]]], *, model: str
    ) -> None:
        if not vectors:
            return
        await self._session.execute(
            _STORE_EMBEDDING,
            [
                {"id": chunk_id, "vector": _vector_literal(vector), "model": model}
                for chunk_id, vector in vectors
            ],
        )

    async def list_chunks(
        self,
        filters: ChunkFilter,
        *,
        user_id: uuid.UUID,
        limit: int,
        after: tuple[uuid.UUID, int] | None = None,
    ) -> tuple[list[ChunkView], bool]:
        """One page of a run's chunks that pass ``filters``, in document order.

        Relational facts (source type, language, publication date) are read from
        their columns; per-chunk facts (format, pages, section) from the chunk
        metadata, where containment is answered by the GIN index.
        """
        bounded = max(1, min(limit, ABSOLUTE_MAX_ROWS))
        chunk = DocumentChunkRow
        page_start = chunk.chunk_metadata["page_start"].as_integer()
        page_end = chunk.chunk_metadata["page_end"].as_integer()

        statement = (
            select(
                chunk.id,
                chunk.document_id,
                chunk.chunk_index,
                chunk.content,
                chunk.token_count,
                chunk.chunk_metadata,
                chunk.embedding_model,
                DocumentRow.language,
                SourceRow.id.label("source_id"),
                SourceRow.source_type,
                SourceRow.url,
            )
            .join(DocumentRow, DocumentRow.id == chunk.document_id)
            .join(SourceRow, SourceRow.id == DocumentRow.source_id)
            .join(ResearchRunRow, ResearchRunRow.id == SourceRow.run_id)
            # Ownership in the WHERE clause, like every other read.
            .where(SourceRow.run_id == filters.run_id, ResearchRunRow.user_id == user_id)
        )

        if filters.source_types:
            statement = statement.where(
                SourceRow.source_type.in_(
                    [source_type.value for source_type in filters.source_types]
                )
            )
        if filters.source_ids:
            statement = statement.where(SourceRow.id.in_(filters.source_ids))
        if filters.document_ids:
            statement = statement.where(chunk.document_id.in_(filters.document_ids))
        if filters.formats:
            statement = statement.where(
                or_(
                    *(
                        chunk.chunk_metadata.contains({"format": fmt.value})
                        for fmt in filters.formats
                    )
                )
            )
        if filters.languages:
            statement = statement.where(DocumentRow.language.in_(filters.languages))
        if filters.section_prefix:
            statement = statement.where(
                chunk.chunk_metadata["section"].astext.startswith(
                    filters.section_prefix, autoescape=True
                )
            )
        # A page filter excludes chunks with no page at all: a Markdown file has
        # no page 3, and returning it for "pages 1-5" would be a wrong answer.
        if filters.page_from is not None:
            statement = statement.where(page_end >= filters.page_from)
        if filters.page_to is not None:
            statement = statement.where(page_start <= filters.page_to)
        if filters.published_after is not None:
            statement = statement.where(SourceRow.published_at >= filters.published_after)
        if filters.published_before is not None:
            statement = statement.where(SourceRow.published_at <= filters.published_before)
        if filters.embedded is True:
            statement = statement.where(chunk.embedding_model.is_not(None))
        elif filters.embedded is False:
            statement = statement.where(chunk.embedding_model.is_(None))

        if after is not None:
            after_document, after_index = after
            statement = statement.where(
                or_(
                    chunk.document_id > after_document,
                    and_(chunk.document_id == after_document, chunk.chunk_index > after_index),
                )
            )

        statement = statement.order_by(chunk.document_id, chunk.chunk_index).limit(bounded + 1)
        rows = list(await self._session.execute(statement))
        return [_to_view(row) for row in rows[:bounded]], len(rows) > bounded


def _to_view(row: Any) -> ChunkView:
    metadata: Mapping[str, Any] = row.chunk_metadata or {}
    raw_format = metadata.get("format")
    return ChunkView(
        id=row.id,
        document_id=row.document_id,
        source_id=row.source_id,
        source_type=SourceType(row.source_type),
        chunk_index=row.chunk_index,
        text=UntrustedText(row.content, source_url=row.url),
        token_count=row.token_count,
        char_start=int(metadata.get("char_start", 0)),
        char_end=int(metadata.get("char_end", 0)),
        page_start=_optional_int(metadata.get("page_start")),
        page_end=_optional_int(metadata.get("page_end")),
        section=metadata.get("section") if isinstance(metadata.get("section"), str) else None,
        format=DocumentFormat(raw_format) if raw_format in set(DocumentFormat) else None,
        language=row.language,
        embedded=row.embedding_model is not None,
    )


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None


def _vector_literal(vector: Sequence[float]) -> str:
    """pgvector's text form: ``[0.1,0.2,...]``. ``repr`` keeps full float precision."""
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"
