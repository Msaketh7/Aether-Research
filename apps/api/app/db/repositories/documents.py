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

* **Both retrieval arms narrow through one filter builder.** Listing chunks,
  keyword search and vector search share ``_chunk_selection``, so a filter
  cannot be honoured by one path and ignored by another - which would put
  material in a report that the caller had excluded.

Bounded like every repository here: each list query carries a LIMIT, and each
read of chunks is scoped to a run the caller owns.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Float,
    Select,
    String,
    and_,
    bindparam,
    cast,
    func,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core.enums import DocumentFormat, SourceType
from app.db.models.research import ResearchRunRow
from app.db.models.source import (
    EMBEDDING_DIMENSIONS,
    TEXT_SEARCH_CONFIG,
    DocumentChunkRow,
    DocumentRow,
    SourceRow,
)
from app.retrieval.filters import ChunkFilter, ChunkView
from app.retrieval.results import ScoredChunk
from app.sources.untrusted import UntrustedText

#: Guard on every list query, even when a caller forgets to pass one.
ABSOLUTE_MAX_ROWS = 200

#: Rows per INSERT when writing chunks. A 2,000-chunk document is ten
#: statements rather than one enormous one or two thousand small ones.
_INSERT_BATCH = 200

#: ``ts_rank_cd`` normalisation: 32 divides the rank by itself plus one, which
#: maps it into [0, 1). A bounded score is what makes a lexical score readable
#: beside a cosine similarity in the same result.
RANK_NORMALIZATION = 32

#: pgvector's own default for ``hnsw.ef_search``, and its ceiling.
HNSW_DEFAULT_EF_SEARCH = 40
HNSW_MAX_EF_SEARCH = 1000

#: How a ranking breaks a tie, for both arms.
#:
#: Not by ``document_id``, which was the first version of this: ids are
#: generated per ingestion, so the same corpus ingested twice ranked its tied
#: chunks differently, and the retrieval benchmark's MRR moved between runs of
#: the same measurement. The source's canonical URL is a property of the
#: document rather than of the row, so the order now survives a re-ingest. The
#: id is kept last only to guarantee a total order.
_TIEBREAK = (SourceRow.canonical_url, DocumentChunkRow.chunk_index, DocumentChunkRow.id)

_STORE_EMBEDDING = sql_text(
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

    async def sources_by_id(
        self, ids: Sequence[uuid.UUID], *, user_id: uuid.UUID
    ) -> list[SourceRow]:
        """The named sources, restricted to ones this user owns.

        Scoped through ``research_runs.user_id`` like every other read here, and
        for the sharper reason: the caller is a research agent, which carries no
        identity of its own and asks for ids it was handed by a retriever.
        """
        if not ids:
            return []
        statement = (
            select(SourceRow)
            .join(ResearchRunRow, ResearchRunRow.id == SourceRow.run_id)
            .where(SourceRow.id.in_(tuple(ids)), ResearchRunRow.user_id == user_id)
            .order_by(SourceRow.canonical_url, SourceRow.id)
        )
        return list((await self._session.execute(statement)).scalars())

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
        """One page of a run's chunks that pass ``filters``, in document order."""
        bounded = _bounded(limit)
        chunk = DocumentChunkRow
        statement = _chunk_selection(filters, user_id=user_id)

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

    # --- retrieval arms (Phase 8) -----------------------------------------

    async def search_lexical(
        self,
        query: str,
        filters: ChunkFilter,
        *,
        user_id: uuid.UUID,
        limit: int,
    ) -> list[ScoredChunk]:
        """The keyword half of hybrid retrieval: the generated ``tsv`` column, ranked.

        The query's terms are combined with OR, not AND - see ``_or_tsquery``,
        which is where the reasoning for that is.

        Scored with ``ts_rank_cd`` under normalisation 32, which maps the rank
        into ``[0, 1)``. Length normalisation is deliberately not applied: these
        are chunks, cut to a near-uniform token count, so correcting for
        document length would correct for a variance chunking has already
        removed.
        """
        bounded = _bounded(limit)
        chunk = DocumentChunkRow
        tsquery = _or_tsquery(query)
        score = func.ts_rank_cd(chunk.tsv, tsquery, RANK_NORMALIZATION).label("score")

        statement = (
            _chunk_selection(filters, user_id=user_id)
            .add_columns(score)
            .where(chunk.tsv.bool_op("@@")(tsquery))
            # Ties are common - ts_rank_cd quantises hard - so the tiebreak is
            # part of the order rather than left to the plan.
            .order_by(score.desc(), *_TIEBREAK)
            .limit(bounded)
        )
        rows = list(await self._session.execute(statement))
        return [ScoredChunk(chunk=_to_view(row), score=float(row.score)) for row in rows]

    async def search_dense(
        self,
        vector: Sequence[float],
        filters: ChunkFilter,
        *,
        user_id: uuid.UUID,
        embedding_model: str,
        limit: int,
    ) -> list[ScoredChunk]:
        """The meaning half: nearest neighbours by cosine distance under pgvector.

        Three things here are not obvious:

        * **Only vectors from ``embedding_model``.** Two models' vectors share a
          column but not a space, so a mixture ranks by nothing. Re-embedding is
          how a deployment changes model, and until it has, the older vectors
          are invisible rather than wrong.
        * **The query vector is bound as text and cast twice**, exactly as the
          write path binds it, so no pgvector codec has to be registered on the
          driver for this to work on every connection.
        * **``hnsw.ef_search`` is raised to cover the request.** It defaults to
          40, so a search for 50 candidates would silently return 40 - and with
          a metadata filter in the WHERE clause, which pgvector applies *after*
          the index scan, fewer still. Widening the search list is what makes
          "top k" mean k.
        """
        bounded = _bounded(limit)
        chunk = DocumentChunkRow
        await self._session.execute(sql_text(f"SET LOCAL hnsw.ef_search = {_ef_search(bounded)}"))

        query_vector = cast(
            cast(bindparam("query_vector", _vector_literal(vector), String), String),
            Vector(EMBEDDING_DIMENSIONS),
        )
        distance = chunk.embedding.op("<=>", return_type=Float)(query_vector).label("distance")

        statement = (
            _chunk_selection(filters, user_id=user_id)
            .add_columns(distance)
            .where(chunk.embedding_model == embedding_model)
            # Ordered by the distance alone: HNSW answers that with an index
            # scan, and a second sort key would turn it back into a full sort
            # over the run. The deterministic tiebreak is applied below, to the
            # k rows that came back.
            .order_by(distance)
            .limit(bounded)
        )
        # The same tiebreak the lexical arm applies, in Python rather than in
        # SQL: a second ORDER BY key would stop HNSW answering the query from
        # its index, and by here there are only k rows to sort.
        rows = sorted(
            await self._session.execute(statement),
            key=lambda row: (float(row.distance), row.url, row.chunk_index, row.id),
        )
        # pgvector's `<=>` is cosine *distance*. Reported as similarity, so that
        # a larger score is a better one here as it is for the lexical arm.
        return [ScoredChunk(chunk=_to_view(row), score=1.0 - float(row.distance)) for row in rows]


def _or_tsquery(query: str) -> ColumnElement[Any]:
    """The query as a tsquery whose terms are OR-ed, built from Postgres's own lexemes.

    ``websearch_to_tsquery`` - the obvious choice, and what this first used -
    combines unquoted words with **AND**. That is right for a web search box and
    wrong here: retrieval is asked questions, not keyword lists, and "how does
    inference pricing compare across providers" then requires one chunk to
    contain every one of those stems. Measured on the fixture corpus, it matched
    nothing at all for exactly that kind of query.

    So the terms are OR-ed. ``to_tsvector`` normalises and stems the query the
    same way the indexed column was built, ``unnest`` takes the lexemes it
    produced, and ``quote_literal`` quotes each one the way tsquery itself
    quotes a lexeme. Nothing is escaped by hand and no user text is concatenated
    into SQL: every term here is a lexeme Postgres produced from a bound
    parameter.

    What is given up, deliberately: the operators ``websearch_to_tsquery``
    understands - quoted phrases, ``or``, a leading ``-`` for exclusion. No
    caller generates them today, and this arm's job in a hybrid retriever is
    recall. Precision is the ranking's job, then fusion's, then the reranker's.

    A query of nothing but stop words produces no lexemes; ``string_agg`` over
    no rows is NULL, ``to_tsquery`` is strict, and ``tsv @@ NULL`` is NULL - so
    it matches nothing, quietly and without a syntax error.
    """
    lexemes = func.unnest(
        func.to_tsvector(TEXT_SEARCH_CONFIG, bindparam("q", query, String))
    ).table_valued("lexeme")
    terms = select(func.string_agg(func.quote_literal(lexemes.c.lexeme), " | ")).scalar_subquery()
    return func.to_tsquery(TEXT_SEARCH_CONFIG, terms)


def _bounded(limit: int) -> int:
    return max(1, min(limit, ABSOLUTE_MAX_ROWS))


def _ef_search(limit: int) -> int:
    """HNSW search-list size for a top-``limit`` query.

    Twice the request, never below pgvector's own default and never above its
    ceiling. Larger is more accurate and slower; the multiplier is a starting
    point for the retrieval benchmark to move, not a tuned value.
    """
    return max(HNSW_DEFAULT_EF_SEARCH, min(limit * 2, HNSW_MAX_EF_SEARCH))


def _chunk_selection(filters: ChunkFilter, *, user_id: uuid.UUID) -> Select[Any]:
    """The columns, joins, ownership check and metadata filters every read shares.

    One function because listing and both retrieval arms have to narrow to
    exactly the same chunks: an arm that quietly ignored a filter would put
    material in a report that the caller had excluded.

    Relational facts (source type, language, publication date) are read from
    their columns; per-chunk facts (format, pages, section) from the chunk
    metadata, where containment is answered by the GIN index.
    """
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
            SourceRow.source_type.in_([source_type.value for source_type in filters.source_types])
        )
    if filters.source_ids:
        statement = statement.where(SourceRow.id.in_(filters.source_ids))
    if filters.document_ids:
        statement = statement.where(chunk.document_id.in_(filters.document_ids))
    if filters.formats:
        statement = statement.where(
            or_(*(chunk.chunk_metadata.contains({"format": fmt.value}) for fmt in filters.formats))
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
    return statement


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
