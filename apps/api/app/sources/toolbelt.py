"""The toolbelt: the six research tools, bounded and recorded.

An agent is handed one of these, not a set of functions. That matters for two
reasons the threat model names directly:

* **Least privilege (TDD 15.3).** A toolbelt is a *capability*, and which tools
  it contains is decided by the caller's role. The synthesizer gets one with no
  network tools at all, because an agent that can be told what to write should
  not also be able to fetch what it is told to fetch. There is no shell tool, no
  filesystem tool and no code-execution tool - not disabled, not present.
* **One choke point.** Every call goes through ``ToolExecutor``: timeout, retry
  with jittered backoff, error classification, and a record per attempt. Adding
  a seventh tool cannot accidentally skip any of that.

Deferred with their own phases, and the seams left where they belong: response
caching (15), the per-run `SearchBudget` (16), OpenTelemetry spans (17), and
semantic near-duplicate clustering (7, with the embeddings it needs).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from app.core.enums import ToolName
from app.core.logging import get_logger
from app.sources.base import CallRecorder, ToolExecutor, ToolResult
from app.sources.http import SafeHttpClient
from app.sources.tools import arxiv, extract, fetch, github, search, sec

logger = get_logger(__name__)

#: What a research agent may do. Read-only, network-bounded, and fixed.
RESEARCH_TOOLS = frozenset(
    {
        ToolName.SEARCH,
        ToolName.FETCH,
        ToolName.PARSE,
        ToolName.SEC_API,
        ToolName.ARXIV_API,
        ToolName.GITHUB_API,
    }
)

#: The synthesizer writes the report from evidence already gathered. Giving it
#: network access would let injected text in a source cause a fetch at the very
#: point where nothing is left to validate the result.
SYNTHESIS_TOOLS: frozenset[ToolName] = frozenset()


@dataclass(frozen=True, slots=True)
class ToolbeltConfig:
    sec_user_agent: str
    github_token: str | None
    search_provider: search.SearchProvider | None


class Toolbelt:
    """The tools an agent is permitted to use."""

    def __init__(
        self,
        *,
        client: SafeHttpClient,
        executor: ToolExecutor,
        config: ToolbeltConfig,
        permitted: frozenset[ToolName] = RESEARCH_TOOLS,
    ) -> None:
        self._client = client
        self._executor = executor
        self._config = config
        self._permitted = permitted
        self._robots = fetch.RobotsCache(client, user_agent="AetherResearch")

    @property
    def permitted(self) -> frozenset[ToolName]:
        return self._permitted

    def restricted_to(self, tools: frozenset[ToolName]) -> Toolbelt:
        """A narrower belt sharing this one's client and executor.

        How a role gets least privilege without a second HTTP client, a second
        connection pool, or a second place where the bounds could differ.
        """
        return Toolbelt(
            client=self._client,
            executor=self._executor,
            config=self._config,
            permitted=self._permitted & tools,
        )

    # --- the six tools ---------------------------------------------------

    async def web_search(
        self, request: search.WebSearchInput
    ) -> ToolResult[search.WebSearchOutput]:
        self._require(ToolName.SEARCH)
        return await self._executor.run(
            ToolName.SEARCH,
            request,
            lambda: search.web_search(
                request, provider=self._config.search_provider, client=self._client
            ),
            summarize=lambda output: {
                "results": len(output.results),
                "domains": len(output.domains),
                "provider": output.provider,
            },
        )

    async def fetch_url(self, request: fetch.FetchUrlInput) -> ToolResult[fetch.FetchedPage]:
        self._require(ToolName.FETCH)
        return await self._executor.run(
            ToolName.FETCH,
            request,
            lambda: fetch.fetch_url(request, client=self._client, robots=self._robots),
            summarize=fetch.summarize,
        )

    async def extract_content(
        self, request: extract.ExtractContentInput
    ) -> ToolResult[extract.ExtractedContent]:
        self._require(ToolName.PARSE)
        # CPU-bound HTML parsing, moved off the event loop: a 5 MB page would
        # otherwise stall every other research task in the process.
        return await self._executor.run(
            ToolName.PARSE,
            request,
            lambda: asyncio.to_thread(extract.extract_content, request),
            summarize=extract.summarize,
        )

    async def search_sec(self, request: sec.SearchSecInput) -> ToolResult[sec.SearchSecOutput]:
        self._require(ToolName.SEC_API)
        return await self._executor.run(
            ToolName.SEC_API,
            request,
            lambda: sec.search_sec(
                request, client=self._client, user_agent=self._config.sec_user_agent
            ),
            summarize=sec.summarize,
        )

    async def search_arxiv(
        self, request: arxiv.SearchArxivInput
    ) -> ToolResult[arxiv.SearchArxivOutput]:
        self._require(ToolName.ARXIV_API)
        return await self._executor.run(
            ToolName.ARXIV_API,
            request,
            lambda: arxiv.search_arxiv(request, client=self._client),
            summarize=arxiv.summarize,
        )

    async def search_github(
        self, request: github.SearchGithubInput
    ) -> ToolResult[github.SearchGithubOutput]:
        self._require(ToolName.GITHUB_API)
        return await self._executor.run(
            ToolName.GITHUB_API,
            request,
            lambda: github.search_github(
                request, client=self._client, token=self._config.github_token
            ),
            summarize=github.summarize,
        )

    async def close(self) -> None:
        await self._client.close()

    # --- internals -------------------------------------------------------

    def _require(self, tool: ToolName) -> None:
        """Refuse a tool this belt does not carry.

        A permission failure, so it is loud and recorded. An agent reaching for a
        tool it was not given is either a bug in the graph or an injected
        instruction being followed, and both need to be visible.
        """
        if tool not in self._permitted:
            logger.error(
                "an agent reached for a tool it was not granted",
                extra={"tool": tool.value, "permitted": sorted(t.value for t in self._permitted)},
            )
            from app.core.errors import Forbidden

            raise Forbidden(
                "That tool is not available to this agent.",
                context={"tool": tool.value},
            )


def build_toolbelt(
    settings: object,
    *,
    recorder: CallRecorder | None = None,
    permitted: frozenset[ToolName] = RESEARCH_TOOLS,
) -> Toolbelt:
    """Assemble a toolbelt from configuration.

    Typed loosely on ``settings`` to keep ``app.sources`` importable without a
    settings instance; the concrete type is ``app.core.config.Settings`` and the
    attributes read here are all declared there.
    """
    from app.core.config import Settings

    assert isinstance(settings, Settings)  # noqa: S101 - narrows for mypy at the boundary

    client = SafeHttpClient(
        connect_timeout_seconds=settings.fetch_connect_timeout_seconds,
        read_timeout_seconds=settings.fetch_read_timeout_seconds,
        max_response_bytes=settings.fetch_max_response_bytes,
        max_redirects=settings.fetch_max_redirects,
        user_agent=settings.sec_user_agent,
        blocked_domains=frozenset(settings.blocked_domains),
        allowed_domains=frozenset(settings.allowed_domains) if settings.allowed_domains else None,
    )
    executor = ToolExecutor(
        recorder=recorder,
        max_attempts=settings.tool_max_attempts,
        timeout_seconds=settings.tool_timeout_seconds,
        max_concurrent_calls=settings.tool_max_concurrent_calls,
    )
    provider = search.build_search_provider(
        preferred=settings.search_provider,
        tavily_api_key=(
            settings.tavily_api_key.get_secret_value() if settings.tavily_api_key else None
        ),
        brave_api_key=(
            settings.brave_api_key.get_secret_value() if settings.brave_api_key else None
        ),
    )

    logger.info(
        "research toolbelt configured",
        extra={
            "search_provider": provider.name if provider else None,
            "github_authenticated": settings.github_token is not None,
            "permitted": sorted(tool.value for tool in permitted),
        },
    )

    return Toolbelt(
        client=client,
        executor=executor,
        config=ToolbeltConfig(
            sec_user_agent=settings.sec_user_agent,
            github_token=(
                settings.github_token.get_secret_value() if settings.github_token else None
            ),
            search_provider=provider,
        ),
        permitted=permitted,
    )


def tool_names() -> Sequence[str]:
    """The complete tool surface, for documentation and the trace UI."""
    return sorted(tool.value for tool in RESEARCH_TOOLS)
