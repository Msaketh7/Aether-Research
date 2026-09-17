"""The ingestion pipeline: bytes to a searchable part of a run's corpus (TDD 8.1).

    bytes -> raw copy in storage -> parse (isolated) -> normalise + hash
          -> language -> chunk -> store source, document, chunks
          -> embed -> store vectors

**Two writes, not one.** The document and its chunks are committed first,
without vectors; embeddings are filled in afterwards, one batch per
transaction. Embedding is the slow, external, failure-prone step, and holding a
transaction open across it would pin a connection for minutes and lose every
chunk to one provider timeout. Written in two steps, a failure part-way leaves a
document that is complete, keyword-searchable and missing only some vectors,
and ``embed_pending`` picks up exactly the ones missing.

**Idempotent at every step.** The raw copy is content-addressed. A source is
found by run and canonical URL before one is created, under an advisory lock. A
document is unique per (source, content hash). Embeddings fill only rows whose
``embedding_model`` is null. Running an ingestion twice - which at-least-once
delivery will eventually do (ADR 0005) - writes nothing new the second time.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.core.enums import DocumentFormat, SourceType
from app.core.logging import get_logger
from app.db.repositories.documents import SqlAlchemyDocumentRepository
from app.db.session import Database
from app.retrieval.artifacts import run_raw_key, stored_content_type
from app.retrieval.chunking import Chunk, Chunker
from app.retrieval.embedding import ChunkEmbedder
from app.retrieval.formats import MEDIA_TYPES
from app.retrieval.isolation import DocumentParser
from app.retrieval.language import LanguageDetection, detect_language
from app.retrieval.parsed import ParsedDocument
from app.sources.credibility import assess
from app.storage import ObjectStorage, content_digest

logger = get_logger(__name__)

#: Characters of the normalised text kept as the source's excerpt.
EXCERPT_CHARS = 280
_MAX_TITLE_CHARS = 500
_MAX_DOMAIN_CHARS = 255


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    """What the caller knows about where a document came from."""

    run_id: UUID
    source_type: SourceType
    url: str
    #: How this source is recognised when it arrives again: a page's canonical
    #: URL, or ``upload://sha256/<hash>`` for an uploaded file.
    canonical_url: str
    domain: str
    publisher: str
    #: When Aether received the bytes. Never inferred.
    accessed_at: dt.datetime
    #: Used when the document does not name itself.
    fallback_title: str
    author: str | None = None
    published_at: dt.datetime | None = None
    #: How these bytes were obtained - the status code, the redirects followed,
    #: whether robots.txt was consulted. Facts about the retrieval, so they are
    #: stored on the document beside the format and the parser. They used to be
    #: written into ``sources.credibility_metadata``, which the ``Source`` DTO
    #: reads as a ``SourceCredibility`` and rejects unknown fields from: the
    #: sources endpoint would have failed on the first real row. Credibility is
    #: assessed here instead, from the source type and the domain.
    fetch_metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PreparedDocument:
    """Everything derived from the bytes, before anything is written."""

    parsed: ParsedDocument
    content_hash: str
    language: LanguageDetection
    chunks: tuple[Chunk, ...]
    token_count: int

    @property
    def normalized(self) -> str:
        return self.parsed.text.expose()


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    source_id: UUID
    document_id: UUID
    chunk_count: int
    #: Chunks embedded by this call.
    embedded: int
    #: Chunks still without a vector: all of them when no embedding model is
    #: configured, some if the provider kept failing.
    pending: int
    #: ``False`` when the document was already stored and this call only resumed.
    created: bool


class DocumentIngestor:
    """Runs the pipeline for one document at a time. Safe to share across tasks."""

    def __init__(
        self,
        *,
        database: Database,
        storage: ObjectStorage,
        parser: DocumentParser,
        chunker: Chunker,
        embedder: ChunkEmbedder | None,
        max_chunks: int,
    ) -> None:
        self._database = database
        self._storage = storage
        self._parser = parser
        self._chunker = chunker
        self._embedder = embedder
        self._max_chunks = max_chunks

    async def prepare(
        self,
        data: bytes,
        *,
        fmt: DocumentFormat,
        charset: str | None,
        source_label: str,
    ) -> PreparedDocument:
        """Parse, detect the language and chunk. Writes nothing."""
        parsed = await self._parser.parse(data, fmt=fmt, charset=charset, source_label=source_label)
        normalized = parsed.text.expose()
        # CPU-bound, so off the event loop: a two-million-character document
        # would otherwise stall every other task in the worker.
        language = await asyncio.to_thread(
            detect_language, normalized, declared=parsed.declared_language
        )
        chunks = await asyncio.to_thread(
            self._chunker.chunk, normalized, fmt=fmt, pages=parsed.pages
        )
        token_count = await asyncio.to_thread(self._chunker.count_tokens, normalized)
        return PreparedDocument(
            parsed=parsed,
            content_hash=content_digest(normalized.encode("utf-8")),
            language=language,
            chunks=tuple(chunks),
            token_count=token_count,
        )

    async def ingest(
        self,
        data: bytes,
        *,
        fmt: DocumentFormat,
        charset: str | None,
        descriptor: SourceDescriptor,
    ) -> IngestionOutcome:
        """Make ``data`` part of the run's corpus. Idempotent; see the module docstring."""
        started = time.perf_counter()

        existing = await self._existing(descriptor)
        if existing is not None:
            source_id, document_id, chunk_count = existing
            embedded, pending = await self.embed_pending(document_id, run_id=descriptor.run_id)
            return IngestionOutcome(
                source_id=source_id,
                document_id=document_id,
                chunk_count=chunk_count,
                embedded=embedded,
                pending=pending,
                created=False,
            )

        raw_key = await self._store_raw(descriptor.run_id, fmt, data, charset)
        prepared = await self.prepare(data, fmt=fmt, charset=charset, source_label=descriptor.url)
        source_id, document_id, created = await self._store(
            prepared, descriptor, fmt=fmt, raw=data, raw_key=raw_key
        )
        embedded, pending = await self.embed_pending(document_id, run_id=descriptor.run_id)

        logger.info(
            "document ingested",
            extra={
                "run_id": str(descriptor.run_id),
                "source_type": descriptor.source_type.value,
                "format": fmt.value,
                "bytes": len(data),
                "chars": len(prepared.normalized),
                "chunks": len(prepared.chunks),
                "tokens": prepared.token_count,
                "language": prepared.language.language,
                "embedded": embedded,
                "pending": pending,
                "created": created,
                "latency_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return IngestionOutcome(
            source_id=source_id,
            document_id=document_id,
            chunk_count=len(prepared.chunks),
            embedded=embedded,
            pending=pending,
            created=created,
        )

    async def embed_pending(self, document_id: UUID, *, run_id: UUID | None) -> tuple[int, int]:
        """Fill in the vectors a document is missing. Returns (embedded now, still pending).

        Each batch is its own read, model call and write, so a failure loses at
        most one batch of work and the next call starts where this one stopped.
        """
        if self._embedder is None:
            return 0, await self._count_pending(document_id)

        embedded = 0
        batch_size = self._embedder.batch_size
        # Bounded: a document has at most max_chunks chunks, and every pass
        # either stores a full batch or ends the loop.
        for _ in range(math.ceil(self._max_chunks / batch_size) + 1):
            async with self._database.session() as session:
                pending = await SqlAlchemyDocumentRepository(session).pending_embeddings(
                    document_id, limit=batch_size
                )
            if not pending:
                return embedded, 0

            vectors = await self._embedder.embed([content for _, content in pending], run_id=run_id)
            async with self._database.session() as session:
                await SqlAlchemyDocumentRepository(session).store_embeddings(
                    [
                        (chunk_id, vector)
                        for (chunk_id, _), vector in zip(pending, vectors, strict=True)
                    ],
                    model=self._embedder.model_label,
                )
            embedded += len(pending)

        remaining = await self._count_pending(document_id)
        logger.warning(
            "embedding stopped at its iteration bound with chunks still pending",
            extra={"document_id": str(document_id), "pending": remaining},
        )
        return embedded, remaining

    # --- internals -------------------------------------------------------

    async def _existing(self, descriptor: SourceDescriptor) -> tuple[UUID, UUID, int] | None:
        """The stored source and document for this descriptor, if ingestion already ran."""
        async with self._database.session() as session:
            repository = SqlAlchemyDocumentRepository(session)
            source = await repository.find_source(
                run_id=descriptor.run_id,
                source_type=descriptor.source_type,
                canonical_url=descriptor.canonical_url,
            )
            if source is None:
                return None
            document = await repository.latest_document(source.id)
            if document is None:
                return None
            return source.id, document.id, int(document.doc_metadata.get("chunk_count", 0))

    async def _store_raw(
        self, run_id: UUID, fmt: DocumentFormat, data: bytes, charset: str | None
    ) -> str:
        key = run_raw_key(run_id, fmt, data)
        await self._storage.upload(
            key,
            data,
            content_type=stored_content_type(MEDIA_TYPES[fmt], charset),
            metadata={"run_id": str(run_id), "sha256": content_digest(data)},
        )
        return key

    async def _store(
        self,
        prepared: PreparedDocument,
        descriptor: SourceDescriptor,
        *,
        fmt: DocumentFormat,
        raw: bytes,
        raw_key: str,
    ) -> tuple[UUID, UUID, bool]:
        """Source, document and chunks in one transaction. Returns (source, document, created)."""
        async with self._database.session() as session:
            repository = SqlAlchemyDocumentRepository(session)
            await repository.lock(
                f"source:{descriptor.run_id}:{descriptor.source_type.value}:"
                f"{descriptor.canonical_url}"
            )
            source = await repository.find_source(
                run_id=descriptor.run_id,
                source_type=descriptor.source_type,
                canonical_url=descriptor.canonical_url,
            )
            if source is None:
                source = await repository.add_source(_source_values(prepared, descriptor))

            document = await repository.document_by_hash(source.id, prepared.content_hash)
            if document is not None:
                return source.id, document.id, False

            document = await repository.add_document(
                {
                    "source_id": source.id,
                    "storage_key": raw_key,
                    "mime_type": MEDIA_TYPES[fmt],
                    "raw_size_bytes": len(raw),
                    "normalized_content": prepared.normalized,
                    "language": prepared.language.language,
                    "extraction_method": prepared.parsed.extraction_method,
                    "token_count": prepared.token_count,
                    "content_hash": prepared.content_hash,
                    "doc_metadata": _compact(
                        {
                            **prepared.parsed.metadata,
                            **descriptor.fetch_metadata,
                            "format": fmt.value,
                            "raw_sha256": content_digest(raw),
                            "chunk_count": len(prepared.chunks),
                            "chunker": self._chunker.label,
                            "language_method": prepared.language.method,
                            "language_confidence": prepared.language.confidence,
                            "declared_language": prepared.parsed.declared_language,
                        }
                    ),
                }
            )
            await repository.add_chunks(
                [
                    {
                        "document_id": document.id,
                        "chunk_index": chunk.index,
                        "content": chunk.text,
                        "token_count": chunk.token_count,
                        "chunk_metadata": _compact(
                            {
                                "char_start": chunk.start,
                                "char_end": chunk.end,
                                "page_start": chunk.page_start,
                                "page_end": chunk.page_end,
                                "section": chunk.section,
                                "source_type": descriptor.source_type.value,
                                "format": fmt.value,
                                "language": prepared.language.language,
                                "chunker": self._chunker.label,
                            }
                        ),
                    }
                    for chunk in prepared.chunks
                ]
            )
            return source.id, document.id, True

    async def _count_pending(self, document_id: UUID) -> int:
        async with self._database.session() as session:
            return await SqlAlchemyDocumentRepository(session).count_pending(document_id)


def _source_values(prepared: PreparedDocument, descriptor: SourceDescriptor) -> dict[str, Any]:
    parsed = prepared.parsed
    credibility = assess(descriptor.source_type, descriptor.domain)
    return {
        "run_id": descriptor.run_id,
        "url": descriptor.url,
        "canonical_url": descriptor.canonical_url,
        "domain": descriptor.domain[:_MAX_DOMAIN_CHARS],
        "source_type": descriptor.source_type.value,
        "title": (parsed.title or descriptor.fallback_title)[:_MAX_TITLE_CHARS],
        "publisher": descriptor.publisher,
        "author": parsed.author or descriptor.author,
        "published_at": descriptor.published_at or _document_date(parsed.published_at),
        "accessed_at": descriptor.accessed_at,
        "content_hash": prepared.content_hash,
        "credibility_score": credibility.score,
        "credibility_metadata": credibility.as_metadata(),
        "excerpt": " ".join(prepared.normalized[: EXCERPT_CHARS * 2].split())[:EXCERPT_CHARS],
    }


def _document_date(value: str | None) -> dt.datetime | None:
    """A document's own date, if it is an unambiguous ISO date. Never a guess."""
    if not value:
        return None
    try:
        parsed = dt.date.fromisoformat(value[:10])
    except ValueError:
        return None
    return dt.datetime(parsed.year, parsed.month, parsed.day, tzinfo=dt.UTC)


def _compact(values: Mapping[str, object]) -> dict[str, object]:
    """Drop unknown values, so an absent fact is absent rather than a stored null."""
    return {key: value for key, value in values.items() if value is not None}
