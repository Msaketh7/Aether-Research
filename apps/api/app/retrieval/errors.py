"""Ingestion failure taxonomy.

The same shape as the tool and model taxonomies, for the same reason: a caller
reacts to *what went wrong*, not to whichever library raised. Two groups, and
the difference decides what happens next:

* **The document's, or the uploader's** - an unsupported or mislabelled format,
  a file over a ceiling, an encrypted PDF, a scan with no text layer. A 4xx at
  the upload boundary; recorded and skipped at ingestion. Never retried: the
  same bytes fail the same way every time.
* **Ours** - offsets that would not point at the text they describe, an
  embedding model whose width does not match the column. Loud, 5xx, and a bug or
  a misconfiguration to fix rather than a document to skip.

``retryable`` is what the worker (Phase 13) reads to decide whether a failed
ingestion job is worth running again.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.core.errors import AppError


class IngestionError(AppError):
    """Base for every ingestion failure."""

    status_code = 422
    code = "ingestion_failed"
    message = "That document could not be ingested."

    #: Whether running the same job again could succeed.
    retryable: bool = False


# --- the document's, or the uploader's ------------------------------------


class EmptyDocument(IngestionError):
    code = "empty_document"
    message = "That file is empty."


class UnsupportedDocumentFormat(IngestionError):
    status_code = 415
    code = "unsupported_document_format"
    message = "That file type is not supported. Upload a PDF, HTML, Markdown or plain-text file."


class DocumentFormatMismatch(IngestionError):
    """The bytes are not what the label says.

    Separate from "unsupported": a PDF sent as ``text/plain`` is a supported
    format with a wrong label, which is either a client bug or someone steering
    the file towards a different parser. Either way it is refused rather than
    silently re-routed, so the label a client sends always means something.
    """

    status_code = 415
    code = "document_format_mismatch"
    message = "The file's contents do not match the type it was sent as."


class UnsupportedTextEncoding(IngestionError):
    status_code = 415
    code = "unsupported_text_encoding"
    message = (
        "That text is not valid UTF-8. Save it as UTF-8, or send its charset in the Content-Type."
    )


class DocumentTooLarge(IngestionError):
    status_code = 413
    code = "document_too_large"
    message = "That document is larger than this service will process."


class DocumentEncrypted(IngestionError):
    code = "document_encrypted"
    message = "That PDF is password-protected. Remove the password and upload it again."


class NoExtractableText(IngestionError):
    code = "no_extractable_text"
    message = (
        "No text could be extracted from that document. "
        "Scanned PDFs need OCR, which this service does not do."
    )


class DocumentUnreadable(IngestionError):
    code = "document_unreadable"
    message = "That document is damaged, or is not the format it claims to be."


class ParseTimeout(IngestionError):
    """The parser process was killed at its deadline.

    The document's problem, not a transient one: pathological input takes as
    long the second time. Hence a 4xx and not retryable.
    """

    code = "parse_timeout"
    message = "That document took too long to read and was abandoned."


# --- ours -----------------------------------------------------------------


class OffsetMismatch(IngestionError):
    """A chunk or page offset would not point at the text it describes.

    The evidence chain resolves a quoted span through these offsets. Storing one
    that is wrong would let a citation point at words the source never
    contained, so it is refused here, where it is a bug, rather than discovered
    by the citation validator, where it would look like a fabrication.
    """

    status_code = 500
    code = "offset_mismatch"
    message = "An internal error stopped this document from being indexed."


class EmbeddingDimensionMismatch(IngestionError):
    """The embedding model's width is not the column's.

    Raised when the pipeline is built, not when it first writes: pgvector would
    refuse every vector, and the failure belongs at startup.
    """

    status_code = 500
    code = "embedding_dimension_mismatch"
    message = "The embedding model is misconfigured."


class EmbeddingResponseInvalid(IngestionError):
    """The provider answered, but not with vectors that could be stored."""

    status_code = 502
    code = "embedding_response_invalid"
    message = "The embedding model returned an unusable response."


#: Every error the parser process can report, by code, so the parent re-raises
#: the same class it would have raised had the parse run in-process.
PARSE_ERRORS: Mapping[str, type[IngestionError]] = {
    error.code: error
    for error in (
        EmptyDocument,
        UnsupportedDocumentFormat,
        DocumentFormatMismatch,
        UnsupportedTextEncoding,
        DocumentTooLarge,
        DocumentEncrypted,
        NoExtractableText,
        DocumentUnreadable,
        ParseTimeout,
        OffsetMismatch,
    )
}
