"""``web_search`` - the tool that finds candidate sources.

The provider is behind an interface, like the LLM providers are, because search
vendors reprice and rate-limit on their own schedule and none of them is worth
coupling the research loop to. Two are implemented: Tavily and Brave.

Every result URL is put through the SSRF guard's canonicaliser before it leaves
this module, and none of them is fetched here. Search returns *candidates*; the
decision to retrieve one is the fetcher's, and it re-validates. A search API is
an untrusted input like any other - a poisoned result set is a cheap way to aim
the fetcher at somewhere it should not go.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from pydantic import Field

from app.core.enums import ToolName
from app.core.logging import get_logger
from app.sources.base import ToolInput
from app.sources.errors import SearchProviderNotConfigured, UpstreamRejected
from app.sources.http import SafeHttpClient
from app.sources.sanitize import sanitize_text
from app.sources.urls import canonicalize, registrable_domain

logger = get_logger(__name__)

TOOL_NAME = ToolName.SEARCH


class WebSearchInput(ToolInput):
    """Strict schema: an agent cannot ask for an unbounded result set."""

    query: str = Field(min_length=3, max_length=400)
    #: Hard ceiling, not advice. An agent that asks for 500 gets a validation
    #: error naming the limit rather than a silently truncated list.
    max_results: int = Field(default=10, ge=1, le=25)
    #: Freshness filter, where the provider supports one.
    recency_days: int | None = Field(default=None, ge=1, le=3650)
    #: Restrict to specific sites. Narrowing only - it can never widen what the
    #: deployment's own domain policy allows.
    include_domains: tuple[str, ...] = ()
    exclude_domains: tuple[str, ...] = ()
    depth: Literal["basic", "advanced"] = "basic"


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One candidate source. Not fetched, not trusted, not yet a source."""

    url: str
    canonical_url: str
    domain: str
    title: str
    #: The provider's own snippet. Untrusted text like any other retrieved
    #: content, and sanitised on the way in - it reaches a model eventually.
    snippet: str
    #: Provider-assigned relevance, where given. ``None`` means *not scored*,
    #: which is not the same as scored zero.
    score: float | None = None
    published_at: str | None = None


@dataclass(frozen=True, slots=True)
class WebSearchOutput:
    query: str
    provider: str
    results: tuple[SearchResult, ...]

    @property
    def domains(self) -> frozenset[str]:
        """Distinct domains, for the diversity checks a researcher applies."""
        return frozenset(result.domain for result in self.results)


class SearchProvider(Protocol):
    """What the tool needs from a search vendor, and nothing more."""

    @property
    def name(self) -> str: ...

    async def search(
        self, request: WebSearchInput, client: SafeHttpClient
    ) -> Sequence[SearchResult]: ...


class TavilySearchProvider:
    """Tavily's search API."""

    ENDPOINT = "https://api.tavily.com/search"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "tavily"

    async def search(
        self, request: WebSearchInput, client: SafeHttpClient
    ) -> Sequence[SearchResult]:
        payload: dict[str, Any] = {
            "query": request.query,
            "max_results": request.max_results,
            "search_depth": request.depth,
        }
        if request.include_domains:
            payload["include_domains"] = list(request.include_domains)
        if request.exclude_domains:
            payload["exclude_domains"] = list(request.exclude_domains)
        if request.recency_days is not None:
            payload["days"] = request.recency_days

        body = await client.post_json(
            self.ENDPOINT,
            payload,
            headers={"Authorization": f"Bearer {self._api_key}"},
        )
        if not isinstance(body, Mapping):
            raise UpstreamRejected("The search provider returned an unexpected shape.")

        return [
            _result(
                url=str(item.get("url", "")),
                title=str(item.get("title", "")),
                snippet=str(item.get("content", "")),
                score=_maybe_float(item.get("score")),
                published_at=_maybe_str(item.get("published_date")),
            )
            for item in _items(body.get("results"))
            if item.get("url")
        ]


class BraveSearchProvider:
    """Brave's search API."""

    ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "brave"

    async def search(
        self, request: WebSearchInput, client: SafeHttpClient
    ) -> Sequence[SearchResult]:
        params = [
            ("q", request.query),
            ("count", str(request.max_results)),
        ]
        if request.recency_days is not None:
            params.append(("freshness", _brave_freshness(request.recency_days)))

        from urllib.parse import urlencode

        body = await client.get_json(
            f"{self.ENDPOINT}?{urlencode(params)}",
            headers={
                "X-Subscription-Token": self._api_key,
                "Accept": "application/json",
            },
        )
        if not isinstance(body, Mapping):
            raise UpstreamRejected("The search provider returned an unexpected shape.")

        web = body.get("web")
        results = web.get("results") if isinstance(web, Mapping) else None
        return [
            _result(
                url=str(item.get("url", "")),
                title=str(item.get("title", "")),
                snippet=str(item.get("description", "")),
                # Brave does not return a relevance score. None, not 0.0:
                # "not scored" and "scored zero" rank very differently.
                score=None,
                published_at=_maybe_str(item.get("age")),
            )
            for item in _items(results)
            if item.get("url")
        ]


async def web_search(
    request: WebSearchInput,
    *,
    provider: SearchProvider | None,
    client: SafeHttpClient,
) -> WebSearchOutput:
    """Find candidate sources for a query.

    Results are canonicalised and de-duplicated here, so a provider returning the
    same article under three tracking URLs yields one candidate. Counting those
    as three would inflate corroboration downstream (TDD 9.3).
    """
    if provider is None:
        raise SearchProviderNotConfigured(
            "No web search provider is configured. Set TAVILY_API_KEY or BRAVE_API_KEY.",
            context={"query": request.query[:100]},
        )

    raw = await provider.search(request, client)

    seen: set[str] = set()
    deduplicated: list[SearchResult] = []
    for result in raw:
        if result.canonical_url in seen:
            continue
        seen.add(result.canonical_url)
        deduplicated.append(result)

    return WebSearchOutput(
        query=request.query,
        provider=provider.name,
        results=tuple(deduplicated[: request.max_results]),
    )


def build_search_provider(
    *,
    preferred: str,
    tavily_api_key: str | None,
    brave_api_key: str | None,
) -> SearchProvider | None:
    """Pick a provider from configuration.

    ``None`` when nothing is configured, so the tool can say precisely which
    setting is missing instead of failing with a 401 from a vendor.
    """
    available: dict[str, SearchProvider] = {}
    if tavily_api_key:
        available["tavily"] = TavilySearchProvider(tavily_api_key)
    if brave_api_key:
        available["brave"] = BraveSearchProvider(brave_api_key)

    if not available:
        return None
    if preferred in available:
        return available[preferred]
    # Configured but not preferred beats not searching at all.
    return next(iter(available.values()))


# --- internals ------------------------------------------------------------


def _result(
    *,
    url: str,
    title: str,
    snippet: str,
    score: float | None,
    published_at: str | None,
) -> SearchResult:
    canonical = canonicalize(url)
    from urllib.parse import urlsplit

    host = (urlsplit(canonical).hostname or "").lower()
    return SearchResult(
        url=url,
        canonical_url=canonical,
        domain=registrable_domain(host),
        # A provider's snippet is retrieved content and reaches a model.
        title=sanitize_text(title)[:300],
        snippet=sanitize_text(snippet)[:1000],
        score=score,
        published_at=published_at,
    )


def _items(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _maybe_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _maybe_str(value: object) -> str | None:
    return str(value) if isinstance(value, str) and value else None


def _brave_freshness(days: int) -> str:
    if days <= 1:
        return "pd"
    if days <= 7:
        return "pw"
    if days <= 31:
        return "pm"
    return "py"
