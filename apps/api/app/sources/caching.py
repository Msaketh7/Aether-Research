"""What of a tool's result is cached, and how it survives the round trip.

The cache machinery (``app.cache``) knows nothing about search results or
fetched pages, and should not: it stores JSON under a content-hash key. This is
where the three web tools say what their key is made of and how their value is
written down - next to the types, so a field added to one of them is added to
its encoding in the same file.

Two properties are load-bearing and neither is incidental:

**The key names every input that changes the answer.** A search is its query,
its result count, its freshness window, its domain filters *and* its provider:
two vendors answering one query are two answers, and a key that omitted the
provider would serve Brave's results as Tavily's after a failover.

**A decoded value is reconstructed, never revived.** ``UntrustedText``
sanitises on construction, so a page read back from the cache goes through the
same constructor a freshly fetched one does. Storing the object graph - by
pickle, or by trusting the stored string - would be a way for content to enter
the system without passing the boundary that exists to clean it (ADR 0011).
"""

from __future__ import annotations

import base64
from typing import Any

from app.cache.keys import CacheNamespace
from app.sources.tools.extract import ExtractContentInput, ExtractedContent
from app.sources.tools.fetch import FetchedPage, FetchUrlInput
from app.sources.tools.search import SearchResult, WebSearchInput, WebSearchOutput
from app.sources.untrusted import UntrustedText

#: Pages above this are not cached. Larger than most articles and far below the
#: fetch ceiling: the value is base64 in JSON, so it costs a third again.
MAX_CACHEABLE_PAGE_BYTES = 512 * 1024


def search_key(request: WebSearchInput, provider: str) -> tuple[Any, ...]:
    """Everything that decides which results come back."""
    return (
        CacheNamespace.SEARCH.value,
        provider,
        request.query,
        request.max_results,
        request.recency_days,
        sorted(request.include_domains),
        sorted(request.exclude_domains),
        request.depth,
    )


def page_key(request: FetchUrlInput) -> tuple[Any, ...]:
    """A page is its URL and the terms it was fetched under.

    ``ignore_robots`` is part of the key on purpose: a page fetched despite
    robots.txt must not be served to a caller that did not ask to override it.
    """
    return (CacheNamespace.PAGE.value, request.url, request.ignore_robots, request.max_bytes)


def extract_key(request: ExtractContentInput) -> tuple[Any, ...]:
    """A pure function of the HTML and the options, so the HTML is the key.

    Hashed by the cache rather than stored, which is what makes it reasonable
    to key on a whole document.
    """
    return (
        CacheNamespace.EXTRACT.value,
        request.html,
        request.source_url,
        request.include_comments,
        request.include_tables,
    )


# --- encodings ------------------------------------------------------------


def encode_search(output: WebSearchOutput) -> dict[str, Any]:
    return {
        "query": output.query,
        "provider": output.provider,
        "results": [
            {
                "url": result.url,
                "canonical_url": result.canonical_url,
                "domain": result.domain,
                "title": result.title,
                "snippet": result.snippet,
                "score": result.score,
                "published_at": result.published_at,
            }
            for result in output.results
        ],
    }


def decode_search(raw: Any) -> WebSearchOutput:
    return WebSearchOutput(
        query=raw["query"],
        provider=raw["provider"],
        results=tuple(
            SearchResult(
                url=result["url"],
                canonical_url=result["canonical_url"],
                domain=result["domain"],
                title=result["title"],
                snippet=result["snippet"],
                score=result["score"],
                published_at=result["published_at"],
            )
            for result in raw["results"]
        ),
    )


def encode_page(page: FetchedPage) -> dict[str, Any] | None:
    """A page, or ``None`` for one too large to be worth a key.

    ``None`` encodes to JSON ``null``, which the policy then stores - so the
    ceiling is enforced here, where the size of the *body* is known, rather
    than after base64 has inflated it. A decoder that reads ``null`` reports a
    miss.
    """
    if len(page.raw_bytes) > MAX_CACHEABLE_PAGE_BYTES:
        return None
    return {
        "url": page.url,
        "final_url": page.final_url,
        "canonical_url": page.canonical_url,
        "domain": page.domain,
        "status_code": page.status_code,
        "content_type": page.content_type,
        "content_hash": page.content_hash,
        "raw_bytes": base64.b64encode(page.raw_bytes).decode("ascii"),
        "body": page.body.expose(),
        "elapsed_ms": page.elapsed_ms,
        "redirects": list(page.redirects),
        "robots_checked": page.robots_checked,
    }


def decode_page(raw: Any) -> FetchedPage:
    if raw is None:
        raise ValueError("the page was too large to cache")
    return FetchedPage(
        url=raw["url"],
        final_url=raw["final_url"],
        canonical_url=raw["canonical_url"],
        domain=raw["domain"],
        status_code=raw["status_code"],
        content_type=raw["content_type"],
        content_hash=raw["content_hash"],
        raw_bytes=base64.b64decode(raw["raw_bytes"]),
        # Through the constructor, so a cached body is sanitised exactly as a
        # fetched one is. See the module docstring.
        body=UntrustedText(raw["body"], source_url=raw["final_url"]),
        # Not the original page's: this call did not wait for a server. What
        # the fetch cost is on the tool call that made it.
        elapsed_ms=0,
        redirects=tuple(raw["redirects"]),
        robots_checked=raw["robots_checked"],
    )


def encode_extract(content: ExtractedContent) -> dict[str, Any]:
    return {
        "text": content.text.expose(),
        "source_url": content.text.source_url,
        "title": content.title,
        "author": content.author,
        "published_at": content.published_at,
        "sitename": content.sitename,
        "language": content.language,
        "method": content.method,
        "char_count": content.char_count,
    }


def decode_extract(raw: Any) -> ExtractedContent:
    return ExtractedContent(
        text=UntrustedText(raw["text"], source_url=raw["source_url"]),
        title=raw["title"],
        author=raw["author"],
        published_at=raw["published_at"],
        sitename=raw["sitename"],
        language=raw["language"],
        method=raw["method"],
        char_count=raw["char_count"],
    )
