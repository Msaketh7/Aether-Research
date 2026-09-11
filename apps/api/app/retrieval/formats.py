"""What a file is, decided by its bytes as well as its label (threat model 3.6).

A client's ``Content-Type`` is a claim, and so is its filename. Both are checked
against the bytes before anything is stored: a file labelled ``application/pdf``
that is really an archive, or "text" that is really a binary, is refused at the
boundary rather than handed to whichever parser the label chose.

Only PDF has a signature worth trusting. HTML, Markdown and plain text are all
just text, and no amount of sniffing tells Markdown from prose - so for those the
label decides *which* text format, and the bytes decide only whether it is text
at all.
"""

from __future__ import annotations

import codecs
import posixpath
import re
import unicodedata
from dataclasses import dataclass

from app.core.enums import DocumentFormat
from app.retrieval.errors import (
    DocumentFormatMismatch,
    EmptyDocument,
    UnsupportedDocumentFormat,
    UnsupportedTextEncoding,
)

#: The media type recorded for each format - canonical, never the client's
#: string, which is attacker-controlled.
MEDIA_TYPES: dict[DocumentFormat, str] = {
    DocumentFormat.PDF: "application/pdf",
    DocumentFormat.HTML: "text/html",
    DocumentFormat.MARKDOWN: "text/markdown",
    DocumentFormat.TEXT: "text/plain",
}

#: What a default filename ends in, when the client sent none.
DEFAULT_EXTENSIONS: dict[DocumentFormat, str] = {
    DocumentFormat.PDF: ".pdf",
    DocumentFormat.HTML: ".html",
    DocumentFormat.MARKDOWN: ".md",
    DocumentFormat.TEXT: ".txt",
}

_DECLARED_TYPES: dict[str, DocumentFormat] = {
    "application/pdf": DocumentFormat.PDF,
    "application/x-pdf": DocumentFormat.PDF,
    "text/html": DocumentFormat.HTML,
    "application/xhtml+xml": DocumentFormat.HTML,
    "text/markdown": DocumentFormat.MARKDOWN,
    "text/x-markdown": DocumentFormat.MARKDOWN,
    "text/plain": DocumentFormat.TEXT,
}

#: Labels that say nothing about the content, so the filename decides.
_UNINFORMATIVE_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})

_EXTENSIONS: dict[str, DocumentFormat] = {
    ".pdf": DocumentFormat.PDF,
    ".html": DocumentFormat.HTML,
    ".htm": DocumentFormat.HTML,
    ".xhtml": DocumentFormat.HTML,
    ".md": DocumentFormat.MARKDOWN,
    ".markdown": DocumentFormat.MARKDOWN,
    ".txt": DocumentFormat.TEXT,
    ".text": DocumentFormat.TEXT,
}

_PDF_HEADER = b"%PDF-"
#: PDF readers accept the header anywhere in the first KiB, so a detector that
#: looked only at byte zero could be walked around by a padded file.
_PDF_HEADER_WINDOW = 1024

#: Signatures worth naming in a refusal. Not the detection itself - the NUL
#: check below catches binaries generally - but "that is a ZIP archive" is an
#: answer a user can act on, and "that is binary" is not.
_BINARY_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"PK\x03\x04", "a ZIP archive (which includes .docx, .xlsx and .pptx)"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "a legacy Microsoft Office file"),
    (b"\x89PNG\r\n\x1a\n", "a PNG image"),
    (b"\xff\xd8\xff", "a JPEG image"),
    (b"GIF87a", "a GIF image"),
    (b"GIF89a", "a GIF image"),
    (b"\x1f\x8b\x08", "a gzip archive"),
    (b"{\\rtf", "an RTF document"),
)

#: How far into a file to look for a NUL byte. Text has none; nearly every
#: binary format has one within its first few KiB.
_BINARY_PROBE_BYTES = 8192

#: Byte-order marks, longest first: the UTF-32 LE mark begins with UTF-16 LE's.
_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF32_LE, "utf-32-le"),
    (codecs.BOM_UTF32_BE, "utf-32-be"),
    (codecs.BOM_UTF8, "utf-8"),
    (codecs.BOM_UTF16_LE, "utf-16-le"),
    (codecs.BOM_UTF16_BE, "utf-16-be"),
)
#: Wide encodings put NUL bytes in ordinary text, so a file that announces one
#: is decoded rather than rejected by the binary probe.
_WIDE_BOMS = tuple(bom for bom, name in _BOMS if not name.startswith("utf-8"))

_CHARSET_NAME = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,39}$")
#: HTML requires a <meta charset> within the first 1024 bytes of the document.
_META_CHARSET = re.compile(
    rb"""<meta[^>]{0,200}?charset\s*=\s*["']?\s*([A-Za-z0-9._:-]{1,40})""", re.IGNORECASE
)
_HTML_START = re.compile(
    rb"^\s*(?:<!doctype\s+html|<html[\s>]|<head[\s>]|<body[\s>])", re.IGNORECASE
)

#: A display name longer than this is not a name.
MAX_FILENAME_CHARS = 200


@dataclass(frozen=True, slots=True)
class DetectedFormat:
    format: DocumentFormat
    media_type: str
    #: The charset the client declared, if it named one Python can decode.
    charset: str | None


def parse_content_type(value: str | None) -> tuple[str, str | None]:
    """``text/plain; charset="UTF-8"`` -> ``("text/plain", "utf-8")``."""
    if not value:
        return "", None
    main, _, parameters = value.partition(";")
    charset: str | None = None
    for parameter in parameters.split(";"):
        key, _, raw = parameter.partition("=")
        if key.strip().lower() == "charset":
            charset = raw.strip().strip("\"'").lower() or None
    return main.strip().lower(), charset


def detect_format(data: bytes, *, content_type: str | None, filename: str | None) -> DetectedFormat:
    """Decide what a file is, or refuse it.

    The label is checked first so that an unsupported type is refused by name,
    then the bytes: a PDF signature overrides nothing, it has to *agree* with
    the label.
    """
    if not data:
        raise EmptyDocument()

    declared, charset = parse_content_type(content_type)
    if declared not in _DECLARED_TYPES and declared not in _UNINFORMATIVE_TYPES:
        raise UnsupportedDocumentFormat(context={"declared": declared[:100]})
    claimed = _DECLARED_TYPES.get(declared) or format_from_filename(filename)

    if _PDF_HEADER in data[:_PDF_HEADER_WINDOW]:
        if claimed is not None and claimed is not DocumentFormat.PDF:
            raise DocumentFormatMismatch(
                f"The file is a PDF but was sent as {claimed.value}.",
                context={"declared": declared, "detected": "pdf"},
            )
        return DetectedFormat(DocumentFormat.PDF, MEDIA_TYPES[DocumentFormat.PDF], None)

    if claimed is DocumentFormat.PDF:
        raise DocumentFormatMismatch(
            "The file was sent as a PDF but is not one.",
            context={"declared": declared, "filename": (filename or "")[:100]},
        )

    _require_text(data)
    resolved = claimed or (
        DocumentFormat.HTML if _HTML_START.match(data[:1024]) else DocumentFormat.TEXT
    )
    return DetectedFormat(resolved, MEDIA_TYPES[resolved], normalise_charset(charset))


def format_from_filename(filename: str | None) -> DocumentFormat | None:
    if not filename:
        return None
    extension = posixpath.splitext(posixpath.basename(filename.replace("\\", "/")).lower())[1]
    return _EXTENSIONS.get(extension)


def normalise_charset(value: str | None) -> str | None:
    """Python's canonical name for a declared charset, or ``None``.

    An unknown charset is dropped rather than refused: decoding then falls back
    to UTF-8, which either works or fails with a message that says so.
    """
    if not value:
        return None
    candidate = value.strip().lower()
    if not _CHARSET_NAME.match(candidate):
        return None
    try:
        return codecs.lookup(candidate).name
    except LookupError:
        return None


def decode_text(data: bytes, *, charset: str | None, fmt: DocumentFormat) -> str:
    """Bytes to ``str``, the way the file says it is encoded, or refuse.

    Order: a byte-order mark (unambiguous), then the declared charset, then an
    HTML ``<meta charset>``, then UTF-8. No guessing beyond that: a wrong guess
    turns every non-ASCII character into mojibake, and mojibake in the stored
    text becomes mojibake in a quoted evidence span.
    """
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            try:
                return data[len(bom) :].decode(encoding)
            except UnicodeDecodeError as exc:
                raise UnsupportedTextEncoding(context={"encoding": encoding}) from exc

    candidates: list[str] = []
    if charset:
        candidates.append(charset)
    if (
        fmt is DocumentFormat.HTML
        and (match := _META_CHARSET.search(data[:1024]))
        and (declared := normalise_charset(match.group(1).decode("ascii"))) is not None
    ):
        candidates.append(declared)
    candidates.append("utf-8")

    for encoding in dict.fromkeys(candidates):
        try:
            return data.decode(encoding)
        # LookupError covers the bytes-to-bytes codecs ("base64", "zlib") that
        # codecs.lookup knows but that are not text encodings.
        except (UnicodeDecodeError, LookupError):
            continue
    raise UnsupportedTextEncoding(context={"tried": list(dict.fromkeys(candidates))})


def display_filename(raw: str | None, fmt: DocumentFormat) -> str:
    """A filename safe to store and to show - never used to build a path.

    Keys are derived from the content hash, so this is display text only. It is
    still cleaned: directory components are dropped (``..\\..\\boot.ini`` is
    someone testing the upload path), control, bidi and zero-width characters
    are removed, and it is bounded.
    """
    name = posixpath.basename((raw or "").replace("\\", "/"))
    name = unicodedata.normalize("NFKC", name)
    name = "".join(character for character in name if unicodedata.category(character)[0] != "C")
    name = name.strip(" .")[:MAX_FILENAME_CHARS].strip()
    return name or f"upload{DEFAULT_EXTENSIONS[fmt]}"


def _require_text(data: bytes) -> None:
    for signature, description in _BINARY_SIGNATURES:
        if data.startswith(signature):
            raise UnsupportedDocumentFormat(
                f"That file is {description}, not a supported document.",
                context={"detected": description},
            )
    if data.startswith(_WIDE_BOMS):
        return
    if b"\x00" in data[:_BINARY_PROBE_BYTES]:
        raise UnsupportedDocumentFormat(
            "That file is binary, not a supported document.",
            context={"detected": "binary"},
        )
