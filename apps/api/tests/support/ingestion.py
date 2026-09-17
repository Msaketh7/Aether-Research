"""Helpers for the tests that run the ingestion pipeline against Postgres."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping

from app.core.enums import SourceType
from app.db.models.research import ResearchRunRow
from app.db.repositories.user import UserRepository
from app.db.session import Database
from app.retrieval.chunking import Chunker
from app.retrieval.embedding import ChunkEmbedder
from app.retrieval.ingestion import DocumentIngestor, SourceDescriptor
from app.retrieval.isolation import InProcessParser
from app.retrieval.parsers import ParseLimits
from app.storage import ObjectStorage

#: Small chunks, so short fixtures produce many of them.
TEST_CHUNK_TOKENS = 64
TEST_OVERLAP_TOKENS = 8
TEST_MAX_CHUNKS = 2000


async def seed_run(
    database: Database, user_id: uuid.UUID | None = None
) -> tuple[uuid.UUID, uuid.UUID]:
    """A user and a queued run they own. Returns ``(user_id, run_id)``."""
    owner = user_id or uuid.uuid4()
    async with database.session() as session:
        await UserRepository(session).ensure(owner, f"{owner}@example.com")
        run = ResearchRunRow(
            user_id=owner,
            title="Test run",
            question="What is this test checking?",
            mode="deep",
            status="queued",
        )
        session.add(run)
        await session.flush()
        run_id = run.id
    return owner, run_id


def in_process_ingestor(
    database: Database,
    storage: ObjectStorage,
    *,
    embedder: ChunkEmbedder | None = None,
) -> DocumentIngestor:
    """The real pipeline with in-process parsing; the isolation tests cover the child."""
    return DocumentIngestor(
        database=database,
        storage=storage,
        parser=InProcessParser(ParseLimits(max_pdf_pages=50, max_chars=500_000)),
        chunker=Chunker(
            chunk_size_tokens=TEST_CHUNK_TOKENS,
            chunk_overlap_tokens=TEST_OVERLAP_TOKENS,
            max_chunks=TEST_MAX_CHUNKS,
        ),
        embedder=embedder,
        max_chunks=TEST_MAX_CHUNKS,
    )


def descriptor(
    run_id: uuid.UUID,
    *,
    canonical_url: str = "upload://sha256/fixture",
    source_type: SourceType = SourceType.UPLOAD,
    domain: str = "uploaded-document",
    published_at: dt.datetime | None = None,
    fetch_metadata: Mapping[str, object] | None = None,
) -> SourceDescriptor:
    return SourceDescriptor(
        run_id=run_id,
        source_type=source_type,
        url="upload://fixture.pdf",
        canonical_url=canonical_url,
        domain=domain,
        publisher="Uploaded document",
        accessed_at=dt.datetime(2026, 9, 10, tzinfo=dt.UTC),
        fallback_title="fixture.pdf",
        published_at=published_at,
        fetch_metadata=fetch_metadata or {},
    )
