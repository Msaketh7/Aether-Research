"""Accepting a user's file: check it, store it, record it (TDD 3.5).

That is all an upload request does. Parsing, chunking and embedding happen in the
worker, when a run that names the file is executed (ADR 0001, ADR 0012). What
*is* done here is everything cheap enough to answer in the response: the size
ceiling, the magic-byte check against the declared type, and - for text formats
- decoding, so a file in an unreadable encoding is refused now rather than
failing a run hours later.
"""

from __future__ import annotations

from uuid import UUID

from app.core.enums import DocumentFormat
from app.core.errors import UploadNotFound
from app.core.logging import get_logger
from app.core.pagination import Page, PageParams, decode_cursor_id, encode_cursor
from app.db.repositories.uploads import SqlAlchemyUploadRepository
from app.retrieval.artifacts import stored_content_type, upload_key
from app.retrieval.errors import DocumentTooLarge
from app.retrieval.formats import decode_text, detect_format, display_filename
from app.retrieval.schemas import UploadedFile
from app.storage import ObjectStorage, content_digest

logger = get_logger(__name__)


class UploadService:
    """The file API's domain logic, composed per request."""

    def __init__(
        self,
        *,
        repository: SqlAlchemyUploadRepository,
        storage: ObjectStorage,
        max_bytes: int,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._max_bytes = max_bytes

    async def store(
        self,
        user_id: UUID,
        data: bytes,
        *,
        content_type: str | None,
        filename: str | None,
    ) -> tuple[UploadedFile, bool]:
        """Keep a file for later ingestion. Returns the record and whether it is new."""
        # The endpoint already stopped reading at the ceiling. Checked again so
        # the service holds its own bound, whoever calls it.
        if len(data) > self._max_bytes:
            raise DocumentTooLarge(context={"bytes": len(data), "limit": self._max_bytes})

        detected = detect_format(data, content_type=content_type, filename=filename)
        if detected.format is not DocumentFormat.PDF:
            decode_text(data, charset=detected.charset, fmt=detected.format)

        digest = content_digest(data)
        existing = await self._repository.get_by_hash(user_id, digest)
        if existing is not None:
            return UploadedFile.model_validate(existing), False

        key = upload_key(user_id, detected.format, data)
        # Bytes first, row second. A row must never point at an object that is
        # not there; the reverse failure - an object with no row - is simply
        # overwritten by the next upload of the same file.
        await self._storage.upload(
            key,
            data,
            content_type=stored_content_type(detected.media_type, detected.charset),
            metadata={
                "user_id": str(user_id),
                "sha256": digest,
                "format": detected.format.value,
            },
        )
        row, created = await self._repository.add(
            user_id=user_id,
            filename=display_filename(filename, detected.format),
            fmt=detected.format.value,
            mime_type=detected.media_type,
            size_bytes=len(data),
            content_hash=digest,
            storage_key=key,
            charset=detected.charset,
        )
        logger.info(
            "document uploaded",
            extra={
                "upload_id": str(row.id),
                "format": detected.format.value,
                "bytes": len(data),
                "created": created,
            },
        )
        return UploadedFile.model_validate(row), created

    async def get(self, user_id: UUID, upload_id: UUID) -> UploadedFile:
        row = await self._repository.get(upload_id, user_id=user_id)
        if row is None:
            # Deliberately indistinguishable from "belongs to someone else".
            raise UploadNotFound()
        return UploadedFile.model_validate(row)

    async def list_for_user(self, user_id: UUID, params: PageParams) -> Page[UploadedFile]:
        after_id = decode_cursor_id(params.cursor) if params.cursor else None
        rows, has_more = await self._repository.list_for_user(
            user_id, limit=params.limit, after_id=after_id
        )
        items = [UploadedFile.model_validate(row) for row in rows]
        next_cursor = encode_cursor(str(items[-1].id)) if has_more and items else None
        return Page(items=items, next_cursor=next_cursor, total=None)
