"""Ingesting the files a run was created with into that run's corpus.

The worker (Phase 13) calls this when it starts a run. Until then it is exercised
by the tests, end to end: upload through the API, create a run naming the upload,
ingest, and read back the source, document and chunks.

Each attached upload becomes an ordinary source of type ``upload`` in the run, so
evidence, citations and the sources page treat it exactly like a fetched page.
A document that cannot be ingested - encrypted, scanned, damaged - is recorded
and skipped, and the others still go in: one bad attachment should cost the run
that file, not the run. Infrastructure failures are not caught here; they
propagate so the worker retries the job, which the pipeline's idempotency makes
safe.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from app.core.enums import DocumentFormat, SourceType
from app.core.logging import get_logger
from app.db.models.upload import UploadRow
from app.db.repositories.uploads import SqlAlchemyUploadRepository
from app.db.session import Database
from app.retrieval.errors import IngestionError
from app.retrieval.ingestion import DocumentIngestor, IngestionOutcome, SourceDescriptor
from app.storage import ObjectStorage

logger = get_logger(__name__)

#: How an uploaded file appears in a run's sources. The frontend already renders
#: an ``upload://`` URL as a document rather than a link.
UPLOAD_DOMAIN = "uploaded-document"
UPLOAD_PUBLISHER = "Uploaded document"


@dataclass(frozen=True, slots=True)
class AttachedUploadResult:
    upload_id: UUID
    outcome: IngestionOutcome | None
    #: The ingestion error code when the file could not be ingested.
    error_code: str | None


def upload_descriptor(run_id: UUID, upload: UploadRow) -> SourceDescriptor:
    """How an upload is recorded as one of a run's sources."""
    return SourceDescriptor(
        run_id=run_id,
        source_type=SourceType.UPLOAD,
        url=f"upload://{upload.filename}",
        canonical_url=f"upload://sha256/{upload.content_hash}",
        domain=UPLOAD_DOMAIN,
        publisher=UPLOAD_PUBLISHER,
        accessed_at=upload.created_at,
        fallback_title=upload.filename,
        # No credibility is passed: ingestion assesses it from the source type
        # and the domain (``app.sources.credibility``), and for an upload the
        # answer is that the origin is the person who asked - primary, and
        # unrated, because nothing in the system has assessed it.
    )


class AttachedUploadIngestion:
    def __init__(
        self, *, database: Database, storage: ObjectStorage, ingestor: DocumentIngestor
    ) -> None:
        self._database = database
        self._storage = storage
        self._ingestor = ingestor

    async def ingest_run(self, run_id: UUID, *, user_id: UUID) -> list[AttachedUploadResult]:
        """Ingest every upload attached to ``run_id``, concurrently.

        Concurrency is bounded below this call: the parser caps processes, the
        gateway caps model calls, and a run has at most ten attachments.
        """
        async with self._database.session() as session:
            uploads = await SqlAlchemyUploadRepository(session).attached_to_run(
                run_id, user_id=user_id
            )

        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(self._ingest_one(run_id, upload)) for upload in uploads]
        return [task.result() for task in tasks]

    async def _ingest_one(self, run_id: UUID, upload: UploadRow) -> AttachedUploadResult:
        try:
            data = await self._storage.download(upload.storage_key)
            outcome = await self._ingestor.ingest(
                data,
                fmt=DocumentFormat(upload.format),
                charset=upload.charset,
                descriptor=upload_descriptor(run_id, upload),
            )
        except IngestionError as exc:
            logger.warning(
                "an attached document could not be ingested",
                extra={
                    "run_id": str(run_id),
                    "upload_id": str(upload.id),
                    "code": exc.code,
                },
            )
            return AttachedUploadResult(upload_id=upload.id, outcome=None, error_code=exc.code)
        return AttachedUploadResult(upload_id=upload.id, outcome=outcome, error_code=None)
