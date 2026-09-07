"""``search_arxiv`` - preprint search over the arXiv API.

The one thing worth being careful about here is that arXiv answers in **XML**,
and parsing untrusted XML with the standard library is a known way to hand an
attacker a file read or a denial of service:

* **XXE** - an external entity declaration that makes the parser fetch
  ``file:///etc/passwd`` or an internal URL. That is an SSRF and a file
  disclosure in one, and it goes straight around this package's URL guard
  because the *parser* makes the request, not the HTTP client.
* **Billion laughs** - nested entity expansion that turns a few kilobytes of
  XML into gigabytes of memory.

``defusedxml`` disables both. It is the reason the dependency exists, and
``xml.etree`` must never be used on a response from outside this system.

arXiv also asks callers not to hammer the API. The executor's concurrency cap
and backoff cover that; the tool itself does not need a limiter of its own.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

from pydantic import Field

from app.core.enums import ToolName
from app.core.logging import get_logger
from app.sources.base import ToolInput
from app.sources.errors import UpstreamRejected
from app.sources.http import SafeHttpClient
from app.sources.sanitize import sanitize_text

logger = get_logger(__name__)

TOOL_NAME = ToolName.ARXIV_API

API_URL = "https://export.arxiv.org/api/query"

_ATOM = "{http://www.w3.org/2005/Atom}"

# The parsed tree is typed `Any`: defusedxml re-exports the stdlib Element with
# no useful stubs, and every field read from it is validated below anyway.


class SearchArxivInput(ToolInput):
    query: str = Field(min_length=2, max_length=400)
    max_results: int = Field(default=10, ge=1, le=50)
    sort_by: Literal["relevance", "submittedDate", "lastUpdatedDate"] = "relevance"
    #: arXiv category, e.g. "cs.LG". Narrows a query that would otherwise return
    #: the whole of physics for a machine-learning question.
    category: str | None = Field(default=None, max_length=40)


@dataclass(frozen=True, slots=True)
class ArxivPaper:
    arxiv_id: str
    title: str
    authors: tuple[str, ...]
    summary: str
    published_at: str | None
    updated_at: str | None
    categories: tuple[str, ...]
    #: The abstract page. Cited in preference to the PDF, because it is stable
    #: and carries the version history.
    url: str
    pdf_url: str | None


@dataclass(frozen=True, slots=True)
class SearchArxivOutput:
    query: str
    papers: tuple[ArxivPaper, ...]
    total_available: int | None


async def search_arxiv(
    request: SearchArxivInput,
    *,
    client: SafeHttpClient,
) -> SearchArxivOutput:
    """Search arXiv for preprints matching a query."""
    search = f"all:{request.query}"
    if request.category:
        search = f"cat:{request.category} AND {search}"

    params = urlencode(
        {
            "search_query": search,
            "start": 0,
            "max_results": request.max_results,
            "sortBy": request.sort_by,
            "sortOrder": "descending",
        }
    )

    response = await client.get(f"{API_URL}?{params}", headers={"Accept": "application/atom+xml"})
    root = _parse_atom(response.text(), url=API_URL)

    total = _total(root)
    papers = tuple(_paper(entry) for entry in root.findall(f"{_ATOM}entry"))

    return SearchArxivOutput(query=request.query, papers=papers, total_available=total)


def _parse_atom(xml: str, *, url: str) -> Any:
    """Parse the Atom feed with external entities and DTDs disabled.

    ``defusedxml``, never ``xml.etree``. See the module docstring: the standard
    parser will happily resolve an entity pointing at the cloud metadata service.
    """
    from defusedxml.common import DefusedXmlException
    from defusedxml.ElementTree import fromstring

    try:
        return fromstring(xml)
    except DefusedXmlException as exc:
        # A response that tries to use entities is hostile, not malformed.
        logger.error(
            "arxiv response contained a disallowed xml construct",
            extra={"url": url, "error": str(exc)},
        )
        raise UpstreamRejected(
            "That API returned XML this tool refuses to parse.",
            context={"url": url, "error": type(exc).__name__},
        ) from exc
    except Exception as exc:
        raise UpstreamRejected(
            "That API returned XML this tool could not parse.",
            context={"url": url, "error": str(exc)},
        ) from exc


def _paper(entry: Any) -> ArxivPaper:
    raw_id = _text(entry, f"{_ATOM}id") or ""
    arxiv_id = raw_id.rsplit("/abs/", 1)[-1] if "/abs/" in raw_id else raw_id

    authors = tuple(
        sanitize_text(name.text or "")[:200]
        for author in entry.findall(f"{_ATOM}author")
        for name in author.findall(f"{_ATOM}name")
        if name.text
    )

    categories = tuple(
        term for category in entry.findall(f"{_ATOM}category") if (term := category.get("term"))
    )

    pdf_url: str | None = None
    for link in entry.findall(f"{_ATOM}link"):
        if link.get("type") == "application/pdf":
            pdf_url = link.get("href")

    return ArxivPaper(
        arxiv_id=arxiv_id,
        title=sanitize_text(_text(entry, f"{_ATOM}title") or "")[:500],
        authors=authors,
        # An abstract is retrieved content and reaches a model.
        summary=sanitize_text(_text(entry, f"{_ATOM}summary") or "")[:4000],
        published_at=_text(entry, f"{_ATOM}published"),
        updated_at=_text(entry, f"{_ATOM}updated"),
        categories=categories,
        url=raw_id or f"https://arxiv.org/abs/{arxiv_id}",
        pdf_url=pdf_url,
    )


def _text(element: Any, path: str) -> str | None:
    found = element.find(path)
    if found is None or found.text is None:
        return None
    return found.text.strip() or None


def _total(root: Any) -> int | None:
    """``None`` when arXiv does not report a total, never zero."""
    node = root.find("{http://a9.com/-/spec/opensearch/1.1/}totalResults")
    if node is None or not node.text:
        return None
    try:
        return int(node.text)
    except ValueError:
        return None


def summarize(output: SearchArxivOutput) -> Mapping[str, object]:
    return {
        "results": len(output.papers),
        "total_available": output.total_available,
        "categories": sorted({c for paper in output.papers for c in paper.categories})[:10],
    }
