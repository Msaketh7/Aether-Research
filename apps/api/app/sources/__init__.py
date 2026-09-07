"""Source discovery and the web content pipeline (Phase 6).

Boundary: the only module allowed to make outbound requests to the open
internet. It owns the SSRF guard, fetch timeouts, content sanitisation and
deduplication. Everything it returns is untrusted data by definition - see
docs/threat-model.md section 3.1.

Import ``Toolbelt`` and ``build_toolbelt``. An agent that imports a tool
function directly bypasses the executor, and with it the timeout, the retry
policy and the call record - so nothing outside this package should.
"""

from __future__ import annotations

from app.sources.base import (
    CallRecorder,
    CollectingToolRecorder,
    LoggingToolRecorder,
    ToolCallRecord,
    ToolExecutor,
    ToolInput,
    ToolResult,
)
from app.sources.errors import (
    ExtractionFailed,
    FetchTimeout,
    ResponseTooLarge,
    RobotsDisallowed,
    SearchProviderNotConfigured,
    ToolError,
    UnsupportedContentType,
    UpstreamRateLimited,
    UpstreamRejected,
    UpstreamUnavailable,
    UrlRefused,
)
from app.sources.http import DEFAULT_USER_AGENT, HttpResponse, SafeHttpClient
from app.sources.sanitize import html_to_text, sanitize_text, strip_invisible_markup
from app.sources.toolbelt import (
    RESEARCH_TOOLS,
    SYNTHESIS_TOOLS,
    Toolbelt,
    ToolbeltConfig,
    build_toolbelt,
    tool_names,
)
from app.sources.tools.arxiv import ArxivPaper, SearchArxivInput, SearchArxivOutput
from app.sources.tools.extract import ExtractContentInput, ExtractedContent
from app.sources.tools.fetch import FetchedPage, FetchUrlInput, RobotsCache
from app.sources.tools.github import GithubRepository, SearchGithubInput, SearchGithubOutput
from app.sources.tools.search import (
    BraveSearchProvider,
    SearchProvider,
    SearchResult,
    TavilySearchProvider,
    WebSearchInput,
    WebSearchOutput,
    build_search_provider,
)
from app.sources.tools.sec import SearchSecInput, SearchSecOutput, SecFiling
from app.sources.untrusted import BEGIN_MARKER, END_MARKER, UntrustedText
from app.sources.urls import (
    ALLOWED_SCHEMES,
    BLOCKED_PORTS,
    ValidatedUrl,
    canonicalize,
    is_blocked_address,
    registrable_domain,
    validate_url,
)

__all__ = [
    "ALLOWED_SCHEMES",
    "BEGIN_MARKER",
    "BLOCKED_PORTS",
    "DEFAULT_USER_AGENT",
    "END_MARKER",
    "RESEARCH_TOOLS",
    "SYNTHESIS_TOOLS",
    "ArxivPaper",
    "BraveSearchProvider",
    "CallRecorder",
    "CollectingToolRecorder",
    "ExtractContentInput",
    "ExtractedContent",
    "ExtractionFailed",
    "FetchTimeout",
    "FetchUrlInput",
    "FetchedPage",
    "GithubRepository",
    "HttpResponse",
    "LoggingToolRecorder",
    "ResponseTooLarge",
    "RobotsCache",
    "RobotsDisallowed",
    "SafeHttpClient",
    "SearchArxivInput",
    "SearchArxivOutput",
    "SearchGithubInput",
    "SearchGithubOutput",
    "SearchProvider",
    "SearchProviderNotConfigured",
    "SearchResult",
    "SearchSecInput",
    "SearchSecOutput",
    "SecFiling",
    "TavilySearchProvider",
    "ToolCallRecord",
    "ToolError",
    "ToolExecutor",
    "ToolInput",
    "ToolResult",
    "Toolbelt",
    "ToolbeltConfig",
    "UnsupportedContentType",
    "UntrustedText",
    "UpstreamRateLimited",
    "UpstreamRejected",
    "UpstreamUnavailable",
    "UrlRefused",
    "ValidatedUrl",
    "WebSearchInput",
    "WebSearchOutput",
    "build_search_provider",
    "build_toolbelt",
    "canonicalize",
    "html_to_text",
    "is_blocked_address",
    "registrable_domain",
    "sanitize_text",
    "strip_invisible_markup",
    "tool_names",
    "validate_url",
]
