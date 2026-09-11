"""What a reader produces: the normalised text, and what the document says about itself."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from app.core.enums import DocumentFormat
from app.retrieval.errors import DocumentUnreadable, OffsetMismatch
from app.sources.untrusted import UntrustedText

#: The value types document metadata may hold. JSON-shaped on purpose: it
#: crosses a process boundary and is stored in a JSONB column.
MetadataValue = str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class PageSpan:
    """Where one PDF page's text sits in the normalised content.

    ``number`` is the page as a reader counts it, from 1. ``[start, end)`` are
    offsets into the normalised string, so ``text[start:end]`` is that page.
    """

    number: int
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    text: UntrustedText
    format: DocumentFormat
    #: Which reader produced the text. ``fallback`` means the HTML readability
    #: pass found nothing and this is a crude tag strip; recorded so a later
    #: stage can weigh the text accordingly instead of trusting it equally.
    extraction_method: str
    title: str | None = None
    author: str | None = None
    #: As the document reports it (an HTML meta date), unparsed. PDFs carry a
    #: creation date, which is when the file was exported rather than when the
    #: work was published, so it is kept in ``metadata`` and never promoted.
    published_at: str | None = None
    #: What the document says its language is (``<html lang>``, PDF ``/Lang``).
    #: Detection happens later and wins when it is confident.
    declared_language: str | None = None
    pages: tuple[PageSpan, ...] = ()
    metadata: Mapping[str, MetadataValue] = field(default_factory=dict)

    def to_wire(self) -> dict[str, object]:
        """The JSON shape the parser process writes to stdout."""
        return {
            "text": self.text.expose(),
            "format": self.format.value,
            "extraction_method": self.extraction_method,
            "title": self.title,
            "author": self.author,
            "published_at": self.published_at,
            "declared_language": self.declared_language,
            "pages": [[page.number, page.start, page.end] for page in self.pages],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_wire(cls, payload: Mapping[str, object], *, source_label: str) -> ParsedDocument:
        """Rebuild what the parser process reported, verifying it on the way in.

        The child is our code, but it ran on hostile input, so its output is
        checked like any other boundary: every field typed, every page span
        inside the text and in order, and the text unchanged by being sanitised
        again. If re-sanitising moved even one character, the page offsets the
        child computed would point at the wrong text.
        """
        raw_text = payload.get("text")
        if not isinstance(raw_text, str) or not raw_text:
            raise DocumentUnreadable(context={"stage": "parser_output", "field": "text"})

        text = UntrustedText(raw_text, source_url=source_label)
        if text.expose() != raw_text:
            raise OffsetMismatch(context={"stage": "parser_output"})

        try:
            document_format = DocumentFormat(str(payload.get("format")))
            pages = _pages(payload.get("pages"), text_length=len(raw_text))
        except (TypeError, ValueError) as exc:
            raise DocumentUnreadable(context={"stage": "parser_output"}) from exc

        raw_metadata = payload.get("metadata")
        metadata: dict[str, MetadataValue] = {}
        if isinstance(raw_metadata, Mapping):
            metadata = {
                str(key): value
                for key, value in raw_metadata.items()
                if value is None or isinstance(value, str | int | float | bool)
            }

        return cls(
            text=text,
            format=document_format,
            extraction_method=_string(payload.get("extraction_method")) or "unknown",
            title=_string(payload.get("title")),
            author=_string(payload.get("author")),
            published_at=_string(payload.get("published_at")),
            declared_language=_string(payload.get("declared_language")),
            pages=pages,
            metadata=metadata,
        )


def _pages(raw: object, *, text_length: int) -> tuple[PageSpan, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise TypeError("pages must be a list")
    pages: list[PageSpan] = []
    previous_end = 0
    for entry in raw:
        if not isinstance(entry, list) or len(entry) != 3:
            raise TypeError("a page span is [number, start, end]")
        number, start, end = (int(value) for value in entry)
        if not (previous_end <= start < end <= text_length) or number < 1:
            raise ValueError("page spans must be ordered, non-empty and inside the text")
        pages.append(PageSpan(number=number, start=start, end=end))
        previous_end = end
    return tuple(pages)


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
