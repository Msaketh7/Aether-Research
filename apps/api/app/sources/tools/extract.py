"""``extract_content`` - pull the article out of the page.

A fetched page is mostly not the article: navigation, cookie banners, related-
story rails, comment threads, share widgets. Feeding all of that to a model
costs tokens for text that carries no evidence, and worse, it dilutes the
signal - an extractor that keeps the sidebar produces claims sourced to a
headline the article never made.

`trafilatura` does the readability pass. Hand-rolling boilerplate removal is a
pile of heuristics that is wrong in a different way on every site, and this is
not the part of the system where the project's value lies.

Two things happen around it that matter more than the extractor choice:

* **Hidden markup is stripped first, not after.** A readability extractor scores
  blocks by text density, so a `display:none` div stuffed with prose can win
  that contest and *become* the main content. Removing it before the extractor
  runs means the extractor sees what a reader would - which makes this a
  prompt-injection control, not a tidiness pass.
* **The result is `UntrustedText`.** It cannot be interpolated into a prompt by
  accident, only passed as delimited data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import Field

from app.core.enums import ToolName
from app.core.logging import get_logger
from app.sources.base import ToolInput
from app.sources.errors import ExtractionFailed
from app.sources.sanitize import html_to_text, sanitize_text, strip_invisible_markup
from app.sources.untrusted import UntrustedText

logger = get_logger(__name__)

TOOL_NAME = ToolName.PARSE

#: Below this, whatever came out is a cookie banner or a paywall stub rather
#: than an article, and calling it "extracted content" would be a lie a citation
#: later depends on.
MIN_USEFUL_CHARS = 200


class ExtractContentInput(ToolInput):
    """Extraction operates on already-fetched bytes, never on a URL.

    Deliberate: a tool that takes a URL is a tool that fetches, and then the
    SSRF guard has two entry points to get right instead of one.
    """

    html: str = Field(min_length=1, max_length=20 * 1024 * 1024)
    source_url: str = Field(min_length=1, max_length=2048)
    include_comments: bool = False
    include_tables: bool = True


@dataclass(frozen=True, slots=True)
class ExtractedContent:
    """The readable article, plus what the page said about itself."""

    text: UntrustedText
    title: str | None
    author: str | None
    published_at: str | None
    sitename: str | None
    language: str | None
    #: How the text was obtained. ``fallback`` means the readability pass found
    #: nothing and this is a crude tag-strip - a caller weighing evidence should
    #: know that, so it is reported rather than smoothed over.
    method: str
    char_count: int


def extract_content(request: ExtractContentInput) -> ExtractedContent:
    """Extract the main content of an HTML document."""
    # Before the extractor, not after. See the module docstring.
    cleaned = strip_invisible_markup(request.html)

    metadata = _extract_metadata(cleaned, request.source_url)
    text, method = _extract_text(cleaned, request)

    if len(text.strip()) < MIN_USEFUL_CHARS:
        raise ExtractionFailed(
            context={
                "url": request.source_url[:200],
                "chars": len(text.strip()),
                "minimum": MIN_USEFUL_CHARS,
                "method": method,
            }
        )

    return ExtractedContent(
        text=UntrustedText(text, source_url=request.source_url),
        title=metadata.get("title"),
        author=metadata.get("author"),
        published_at=metadata.get("date"),
        sitename=metadata.get("sitename"),
        language=metadata.get("language"),
        method=method,
        char_count=len(text),
    )


def _extract_text(html: str, request: ExtractContentInput) -> tuple[str, str]:
    """Readability first, crude tag-strip as a stated fallback."""
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html,
            include_comments=request.include_comments,
            include_tables=request.include_tables,
            include_links=False,
            # No network, ever. trafilatura can be asked to fetch things it
            # finds; that would be an SSRF path straight around the guard.
            no_fallback=False,
            favor_precision=True,
            url=None,
        )
    except Exception as exc:  # pragma: no cover - parser-dependent
        logger.warning(
            "readability extraction raised; falling back to tag stripping",
            extra={"url": request.source_url[:200], "error": str(exc)},
        )
        extracted = None

    if extracted and len(extracted.strip()) >= MIN_USEFUL_CHARS:
        return sanitize_text(extracted), "readability"

    return html_to_text(html), "fallback"


def _extract_metadata(html: str, source_url: str) -> dict[str, str | None]:
    """Best-effort metadata. Every field is allowed to be unknown.

    ``None`` rather than a guess: `published_at` feeds recency weighting, and an
    invented date is worse than an absent one because it will be believed.
    """
    empty: dict[str, str | None] = {
        "title": None,
        "author": None,
        "date": None,
        "sitename": None,
        "language": None,
    }
    try:
        import trafilatura

        metadata = trafilatura.extract_metadata(html, default_url=source_url)
    except Exception:  # pragma: no cover - parser-dependent
        return empty
    # trafilatura's annotation says this cannot be None, so mypy calls the guard
    # unreachable. It stays: the annotation is a third-party promise about a
    # function that parses hostile HTML, and being wrong here would be an
    # AttributeError in the middle of a research run.
    if metadata is None:
        return empty  # type: ignore[unreachable]

    return {
        "title": _clean(getattr(metadata, "title", None)),
        "author": _clean(getattr(metadata, "author", None)),
        "date": _clean(getattr(metadata, "date", None)),
        "sitename": _clean(getattr(metadata, "sitename", None)),
        "language": _clean(getattr(metadata, "language", None)),
    }


def _clean(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    cleaned = sanitize_text(value)[:300]
    return cleaned or None


def summarize(content: ExtractedContent) -> Mapping[str, object]:
    return {
        "chars": content.char_count,
        "method": content.method,
        "has_title": content.title is not None,
        "has_date": content.published_at is not None,
        "language": content.language,
    }
