"""``fetch_url`` - retrieve a page, safely, and keep the raw bytes.

Everything dangerous about this operation is already handled by
``SafeHttpClient``: the SSRF guard, redirect re-validation, the peer-address
check, the size cap and the timeouts. What is left here is the policy layer:

* **robots.txt** (TDD 9.2). Fetched once per origin and cached for the life of
  the client. Being a well-behaved crawler is not decoration - a research tool
  that ignores it gets its IP blocked and its operator a complaint, and the
  cost of respecting it is one small request per domain.
* **Content type.** A PDF is a perfectly good source, but it is not text and
  this tool does not pretend otherwise; it says so and Phase 7's ingestion
  pipeline handles it.
* **The raw body is preserved** alongside the extracted text, because
  ``documents.storage_key`` points at the archived original and an extraction
  can then be re-run or audited without re-fetching - which also means without
  trusting the network to still serve the same bytes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.robotparser import RobotFileParser

from pydantic import Field

from app.core.enums import ToolName
from app.core.logging import get_logger
from app.sources.base import ToolInput
from app.sources.errors import RobotsDisallowed, ToolError, UnsupportedContentType
from app.sources.http import SafeHttpClient
from app.sources.untrusted import UntrustedText
from app.sources.urls import canonicalize, registrable_domain

logger = get_logger(__name__)

TOOL_NAME = ToolName.FETCH

#: Content types this tool will read as text. Anything else is a legitimate
#: source that belongs to a different pipeline, not a failure.
TEXT_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "text/plain",
        "text/markdown",
        "application/json",
        "application/xml",
        "text/xml",
    }
)


class FetchUrlInput(ToolInput):
    url: str = Field(min_length=8, max_length=2048)
    #: Overriding robots.txt is possible but never the default, and it is
    #: recorded, so "we crawled something we were asked not to" is a decision
    #: someone made rather than a default nobody noticed.
    ignore_robots: bool = False
    max_bytes: int | None = Field(default=None, ge=1024, le=25 * 1024 * 1024)


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """A retrieved page. The body is untrusted by construction."""

    url: str
    final_url: str
    canonical_url: str
    domain: str
    status_code: int
    content_type: str
    #: sha256 of the raw bytes - the idempotency key for ingestion and the
    #: value `documents.content_hash` stores.
    content_hash: str
    raw_bytes: bytes
    #: The decoded body, typed so it cannot be interpolated into a prompt.
    body: UntrustedText
    elapsed_ms: int
    redirects: tuple[str, ...]
    robots_checked: bool


class RobotsCache:
    """One robots.txt per origin, fetched once.

    Failures are permissive: a site whose robots.txt 404s or times out has not
    forbidden anything, and refusing to read the whole domain because a
    politeness file was unreachable would be its own kind of wrong.
    """

    def __init__(self, client: SafeHttpClient, user_agent: str) -> None:
        self._client = client
        self._user_agent = user_agent
        self._parsers: dict[str, RobotFileParser | None] = {}

    async def allows(self, url: str) -> bool:
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"

        if origin not in self._parsers:
            self._parsers[origin] = await self._load(origin)

        parser = self._parsers[origin]
        if parser is None:
            return True
        return parser.can_fetch(self._user_agent, url)

    async def _load(self, origin: str) -> RobotFileParser | None:
        try:
            response = await self._client.get(f"{origin}/robots.txt", max_bytes=512 * 1024)
        except ToolError:
            # No robots.txt, or unreachable. Nothing has been forbidden.
            return None

        parser = RobotFileParser()
        try:
            parser.parse(response.text().splitlines())
        except Exception:  # pragma: no cover - the stdlib parser is lenient
            return None
        return parser


async def fetch_url(
    request: FetchUrlInput,
    *,
    client: SafeHttpClient,
    robots: RobotsCache | None = None,
) -> FetchedPage:
    """Retrieve a URL as text, or explain why it cannot be."""
    robots_checked = False
    if robots is not None and not request.ignore_robots:
        robots_checked = True
        if not await robots.allows(request.url):
            raise RobotsDisallowed(
                context={"url": request.url[:200], "reason": "robots_txt"},
            )
    elif request.ignore_robots:
        logger.warning(
            "fetching with robots.txt explicitly ignored",
            extra={"url": request.url[:200]},
        )

    response = await client.get(request.url, max_bytes=request.max_bytes)

    if response.content_type and response.content_type not in TEXT_CONTENT_TYPES:
        raise UnsupportedContentType(
            context={
                "url": response.final_url,
                "content_type": response.content_type,
                # Named so a caller can route it to the right pipeline rather
                # than treat it as a dead end.
                "handled_by": "document ingestion (Phase 7)",
            }
        )

    from urllib.parse import urlsplit

    canonical = canonicalize(response.final_url)
    host = (urlsplit(canonical).hostname or "").lower()

    return FetchedPage(
        url=request.url,
        final_url=response.final_url,
        canonical_url=canonical,
        domain=registrable_domain(host),
        status_code=response.status_code,
        content_type=response.content_type,
        content_hash=hashlib.sha256(response.content).hexdigest(),
        raw_bytes=response.content,
        body=UntrustedText(response.text(), source_url=response.final_url),
        elapsed_ms=response.elapsed_ms,
        redirects=response.redirects,
        robots_checked=robots_checked,
    )


def summarize(page: FetchedPage) -> Mapping[str, object]:
    """What of a fetch belongs in the tool-call record.

    Never the body. A trace row that carries a whole page is a trace row that
    stops being readable, and the body is archived under its own key anyway.
    """
    return {
        "final_url": page.final_url,
        "status": page.status_code,
        "content_type": page.content_type,
        "bytes": len(page.raw_bytes),
        "content_hash": page.content_hash,
        "redirects": len(page.redirects),
        "robots_checked": page.robots_checked,
    }
