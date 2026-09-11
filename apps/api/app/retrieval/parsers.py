"""Bytes to normalised text, one reader per format (ADR 0012).

Every function here runs on hostile input, and in the worker it runs inside the
child process ``app.retrieval.isolation`` starts, not in the worker itself.
Nothing here reads configuration, touches the network, or writes a file.

The text a reader returns is the document's **normalised content**: the exact
string stored in ``documents.normalized_content``, split into chunks, hashed,
and quoted by evidence spans. It is produced once, here, through
``UntrustedText`` (sanitised, delimiter-safe), and never rewritten afterwards.
PDF page boundaries are measured on that finished string, so the page number a
chunk reports is exact.
"""

from __future__ import annotations

import datetime as dt
import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.core.enums import DocumentFormat
from app.retrieval.errors import (
    DocumentEncrypted,
    DocumentTooLarge,
    DocumentUnreadable,
    IngestionError,
    NoExtractableText,
)
from app.retrieval.formats import decode_text
from app.retrieval.parsed import MetadataValue, PageSpan, ParsedDocument
from app.sources.sanitize import sanitize_text
from app.sources.untrusted import UntrustedText

if TYPE_CHECKING:
    from pypdf import PdfReader

#: Joins PDF pages: a blank line, so a sentence broken across pages is not
#: glued into one word, and so the chunker sees a paragraph boundary there.
PAGE_SEPARATOR = "\n\n"

#: Metadata strings are display text; anything longer is not a title.
MAX_METADATA_CHARS = 300

#: ``<html lang="de-CH">``, which trafilatura's metadata does not report.
_HTML_LANG = re.compile(r"<html\b[^>]*?\blang\s*=\s*[\"']?([A-Za-z]{2,3})\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ParseLimits:
    max_pdf_pages: int
    max_chars: int


def parse_document(
    data: bytes,
    *,
    fmt: DocumentFormat,
    charset: str | None,
    source_label: str,
    limits: ParseLimits,
) -> ParsedDocument:
    """Read ``data`` as ``fmt``. Raises from ``app.retrieval.errors`` only."""
    if fmt is DocumentFormat.PDF:
        return parse_pdf(data, source_label=source_label, limits=limits)
    if fmt is DocumentFormat.HTML:
        return parse_html(data, charset=charset, source_label=source_label, limits=limits)
    if fmt is DocumentFormat.MARKDOWN:
        return parse_markdown(data, charset=charset, source_label=source_label, limits=limits)
    return parse_text(data, charset=charset, source_label=source_label, limits=limits)


# --- PDF --------------------------------------------------------------------


def parse_pdf(data: bytes, *, source_label: str, limits: ParseLimits) -> ParsedDocument:
    """Text per page with pypdf, then the pages joined with their offsets recorded.

    A page with no text - a full-page figure, a scanned image - is skipped
    rather than given an empty span, so every page number that remains is one a
    reader could actually quote from.
    """
    from pypdf import PasswordType
    from pypdf import PdfReader as Reader

    try:
        reader = Reader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                opened = reader.decrypt("")
            except Exception as exc:
                # An AES file without the optional crypto dependency lands here
                # too. To the user it is the same answer: remove the password.
                raise DocumentEncrypted(context={"error": type(exc).__name__}) from exc
            # A file with only an owner password opens with an empty user
            # password. One with a user password does not.
            if opened == PasswordType.NOT_DECRYPTED:
                raise DocumentEncrypted()
        page_count = len(reader.pages)
    except IngestionError:
        raise
    except Exception as exc:  # pypdf raises a wide, version-dependent set
        raise _unreadable(exc) from exc

    if page_count > limits.max_pdf_pages:
        raise DocumentTooLarge(
            f"That PDF has {page_count:,} pages; the limit is {limits.max_pdf_pages:,}.",
            context={"pages": page_count, "max_pages": limits.max_pdf_pages},
        )

    parts: list[str] = []
    spans: list[PageSpan] = []
    cursor = 0
    for number, page in enumerate(reader.pages, start=1):
        try:
            raw = page.extract_text() or ""
        except Exception as exc:
            raise _unreadable(exc, page=number) from exc
        cleaned = UntrustedText(raw, source_url=source_label).expose()
        if not cleaned:
            continue
        if parts:
            cursor += len(PAGE_SEPARATOR)
        spans.append(PageSpan(number=number, start=cursor, end=cursor + len(cleaned)))
        parts.append(cleaned)
        cursor += len(cleaned)
        # Per page, so an enormous document is refused as soon as it passes the
        # limit rather than after all of it is held in memory.
        _check_length(cursor, limits)

    if not parts:
        raise NoExtractableText(context={"pages": page_count})

    title, author, metadata = _pdf_metadata(reader)
    return ParsedDocument(
        text=UntrustedText(PAGE_SEPARATOR.join(parts), source_url=source_label),
        format=DocumentFormat.PDF,
        extraction_method="pypdf",
        title=title,
        author=author,
        declared_language=_pdf_language(reader),
        pages=tuple(spans),
        metadata={"page_count": page_count, "pages_with_text": len(spans), **metadata},
    )


def _pdf_metadata(reader: PdfReader) -> tuple[str | None, str | None, dict[str, MetadataValue]]:
    """Title and author from the document information dictionary.

    Best effort: every field may be missing or malformed, and none is guessed.
    The creation date is kept as metadata rather than promoted to a publication
    date - it is when the file was exported, which is often years away from
    when the work in it was published.
    """
    info: Any = _attempt(lambda: reader.metadata)
    if info is None:
        return None, None, {}

    extra: dict[str, MetadataValue] = {}
    if producer := _clean(_attempt(lambda: info.producer)):
        extra["pdf_producer"] = producer
    created = _attempt(lambda: info.creation_date)
    if isinstance(created, dt.datetime):
        extra["pdf_creation_date"] = created.isoformat()
    return _clean(_attempt(lambda: info.title)), _clean(_attempt(lambda: info.author)), extra


def _pdf_language(reader: PdfReader) -> str | None:
    """The catalog's ``/Lang`` entry, which a tagged PDF carries."""
    try:
        root: Any = reader.trailer["/Root"]
        value = root.get("/Lang")
    except Exception:
        return None
    return _language_code(str(value)) if value else None


# --- HTML -------------------------------------------------------------------


def parse_html(
    data: bytes, *, charset: str | None, source_label: str, limits: ParseLimits
) -> ParsedDocument:
    """The article, not the page - through the same extractor fetched pages use.

    Reusing it is the point. Hidden elements are stripped *before* the
    readability pass (Phase 6), so a ``display:none`` paragraph written for a
    model cannot win the text-density contest and become the main content of an
    uploaded file any more than of a fetched one.
    """
    from pydantic import ValidationError

    from app.sources.errors import ExtractionFailed
    from app.sources.tools.extract import ExtractContentInput, extract_content

    html = decode_text(data, charset=charset, fmt=DocumentFormat.HTML)
    if not html.strip():
        raise NoExtractableText("That file contains no text.")
    try:
        request = ExtractContentInput(html=html, source_url=source_label[:2048])
    except ValidationError as exc:
        # Length is the only thing that can fail here: the source label is
        # truncated to fit and the document is known not to be blank.
        raise DocumentTooLarge(context={"chars": len(html)}) from exc
    try:
        extracted = extract_content(request)
    except ExtractionFailed as exc:
        raise NoExtractableText(
            "No readable article text could be found in that page.", context=exc.context
        ) from exc

    _check_length(len(extracted.text), limits)
    return ParsedDocument(
        text=extracted.text,
        format=DocumentFormat.HTML,
        extraction_method=extracted.method,
        title=extracted.title,
        author=extracted.author,
        published_at=extracted.published_at,
        declared_language=_language_code(extracted.language) or _html_language(html),
        metadata={"sitename": extracted.sitename},
    )


def _html_language(html: str) -> str | None:
    """The root element's ``lang`` attribute, if the page declares one."""
    match = _HTML_LANG.search(html[:4096])
    return _language_code(match.group(1)) if match else None


# --- Markdown and plain text ------------------------------------------------


def parse_markdown(
    data: bytes, *, charset: str | None, source_label: str, limits: ParseLimits
) -> ParsedDocument:
    """The Markdown source itself is the normalised text.

    Headings are structure the chunker uses, and stripping the syntax would
    move every offset for no gain: a quoted span reads the same either way.
    """
    text = _decoded(data, charset=charset, fmt=DocumentFormat.MARKDOWN, label=source_label)
    _check_length(len(text), limits)
    return ParsedDocument(
        text=text,
        format=DocumentFormat.MARKDOWN,
        extraction_method="markdown",
        title=_markdown_title(text.expose()),
    )


def parse_text(
    data: bytes, *, charset: str | None, source_label: str, limits: ParseLimits
) -> ParsedDocument:
    text = _decoded(data, charset=charset, fmt=DocumentFormat.TEXT, label=source_label)
    _check_length(len(text), limits)
    return ParsedDocument(text=text, format=DocumentFormat.TEXT, extraction_method="plaintext")


def _decoded(data: bytes, *, charset: str | None, fmt: DocumentFormat, label: str) -> UntrustedText:
    text = UntrustedText(decode_text(data, charset=charset, fmt=fmt), source_url=label)
    if not text:
        raise NoExtractableText("That file contains no text.")
    return text


def _markdown_title(text: str) -> str | None:
    """The first level-one ATX heading outside a code fence, if there is one."""
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if not in_fence and stripped.startswith("# "):
            return _clean(stripped[2:].strip().rstrip("#").strip())
    return None


# --- shared -----------------------------------------------------------------


def _check_length(chars: int, limits: ParseLimits) -> None:
    if chars > limits.max_chars:
        raise DocumentTooLarge(
            f"That document has more than {limits.max_chars:,} characters of text.",
            context={"chars": chars, "max_chars": limits.max_chars},
        )


def _attempt[T](read: Callable[[], T]) -> T | None:
    """``read()``, or ``None`` if it raises.

    For metadata only. A hostile file can make any single field malformed, and
    a missing title is not a reason to refuse the document.
    """
    try:
        return read()
    except Exception:
        return None


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = sanitize_text(value)[:MAX_METADATA_CHARS].strip()
    return cleaned or None


def _language_code(value: str | None) -> str | None:
    """``en-US`` to ``en``. Anything that is not a plausible code is dropped."""
    if not value:
        return None
    primary = value.strip().replace("_", "-").split("-")[0].lower()
    if 2 <= len(primary) <= 3 and primary.isascii() and primary.isalpha():
        return primary
    return None


def _unreadable(exc: BaseException, *, page: int | None = None) -> DocumentUnreadable:
    context: dict[str, object] = {"error": type(exc).__name__, "detail": str(exc)[:200]}
    if page is not None:
        context["page"] = page
    return DocumentUnreadable(context=context)
