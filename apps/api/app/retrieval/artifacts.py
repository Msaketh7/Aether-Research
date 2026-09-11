"""Where an ingested file's original bytes are kept (ADR 0010, ADR 0012).

The bytes as they arrived are kept alongside the text extracted from them, so a
parse can be re-run or audited without trusting the uploader - or the network -
to supply the same file again.
"""

from __future__ import annotations

from uuid import UUID

from app.core.enums import DocumentFormat
from app.storage.keys import (
    ArtifactKind,
    content_addressed_name,
    run_artifact_key,
    upload_artifact_key,
)

#: The artifact kind that holds each format's original bytes.
RAW_ARTIFACT_KINDS: dict[DocumentFormat, ArtifactKind] = {
    DocumentFormat.PDF: ArtifactKind.PDF,
    DocumentFormat.HTML: ArtifactKind.RAW_HTML,
    DocumentFormat.MARKDOWN: ArtifactKind.MARKDOWN,
    DocumentFormat.TEXT: ArtifactKind.TEXT,
}


def upload_key(user_id: UUID, fmt: DocumentFormat, data: bytes) -> str:
    """Content-addressed under the user's prefix, so re-uploading is idempotent."""
    kind = RAW_ARTIFACT_KINDS[fmt]
    return upload_artifact_key(user_id=user_id, kind=kind, name=content_addressed_name(kind, data))


def run_raw_key(run_id: UUID, fmt: DocumentFormat, data: bytes) -> str:
    """The run's own copy. Deleting the run deletes it; deleting the upload does not."""
    kind = RAW_ARTIFACT_KINDS[fmt]
    return run_artifact_key(run_id=run_id, kind=kind, name=content_addressed_name(kind, data))


def stored_content_type(media_type: str, charset: str | None) -> str:
    """The type stored with the bytes, carrying the uploader's charset when one was declared."""
    return f"{media_type}; charset={charset}" if charset else media_type
