"""File API DTOs. They serialise to ``UploadedFile`` in ``@aether/shared-types``."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from app.core.enums import DocumentFormat
from app.research.schemas import ApiModel


class UploadedFile(ApiModel):
    """A file the caller uploaded. ``id`` is what ``document_ids`` refers to."""

    id: UUID
    filename: str
    format: DocumentFormat
    mime_type: str
    size_bytes: int
    #: SHA-256 of the bytes as uploaded. The same file uploaded twice is one upload.
    content_hash: str
    created_at: datetime
