"""Assembling the ingestion pipeline from configuration.

For the worker (Phase 13) and for tests. The API process never calls this: it
stores uploads and does not parse them (ADR 0001).
"""

from __future__ import annotations

from app.core.config import Settings
from app.db.session import Database
from app.models import LLMGateway
from app.retrieval.chunking import Chunker
from app.retrieval.embedding import ChunkEmbedder
from app.retrieval.ingestion import DocumentIngestor
from app.retrieval.isolation import DocumentParser, IsolatedParser
from app.retrieval.parsers import ParseLimits
from app.storage import ObjectStorage


def parse_limits(settings: Settings) -> ParseLimits:
    return ParseLimits(max_pdf_pages=settings.max_pdf_pages, max_chars=settings.max_document_chars)


def build_chunker(settings: Settings) -> Chunker:
    return Chunker(
        chunk_size_tokens=settings.chunk_size_tokens,
        chunk_overlap_tokens=settings.chunk_overlap_tokens,
        max_chunks=settings.max_chunks_per_document,
    )


def build_document_ingestor(
    settings: Settings,
    *,
    database: Database,
    storage: ObjectStorage,
    gateway: LLMGateway | None,
    parser: DocumentParser | None = None,
) -> DocumentIngestor:
    """The production pipeline: isolated parsing, configured chunking, gateway embeddings.

    ``gateway=None`` builds a pipeline that stores documents and chunks and
    leaves every vector pending - keyword-searchable now, embeddable later by
    ``embed_pending`` - for a deployment with no embedding model configured.
    """
    return DocumentIngestor(
        database=database,
        storage=storage,
        parser=parser
        or IsolatedParser(
            limits=parse_limits(settings),
            timeout_seconds=settings.parse_timeout_seconds,
            max_memory_bytes=settings.parse_max_memory_bytes,
            max_concurrent=settings.max_concurrent_parses,
        ),
        chunker=build_chunker(settings),
        embedder=(
            ChunkEmbedder(gateway, batch_size=settings.embedding_batch_size)
            if gateway is not None
            else None
        ),
        max_chunks=settings.max_chunks_per_document,
    )
