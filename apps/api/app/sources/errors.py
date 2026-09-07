"""Tool failure taxonomy.

Mirrors ``app/models/errors.py`` in shape and for the same reason: a caller
reacts to *what went wrong*, not to whichever library raised. The distinctions
that change behaviour:

* **Retryable** - a timeout, a 5xx, a dropped connection. Back off and try again.
* **Rate limited** - retryable, but the provider usually tells us when.
* **Refused** - the URL is not one we are permitted to fetch. Never retried,
  never followed, and loud, because it is either an attack or a bug.
* **Not retryable** - a 404, a page that is not text, a malformed query.

``UrlRefused`` is the one that matters most. It is raised by the SSRF guard, and
it must never be quietly swallowed into "this source didn't work out": a run that
silently drops a blocked-metadata-endpoint fetch looks identical to one that
successfully defended against an attack, and the difference is the whole point of
having the guard.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import AppError


class ToolError(AppError):
    """Base for every research-tool failure."""

    status_code = 502
    code = "tool_error"
    message = "A research tool could not complete this request."

    retryable: bool = False


class UrlRefused(ToolError):
    """The SSRF guard rejected this URL.

    A security event, not a fetch failure. It is recorded at warning level with
    the reason and the offending host, because a run that keeps producing these
    is either under attack or being fed injected instructions.
    """

    status_code = 400
    code = "url_refused"
    message = "That URL is not permitted."

    def __init__(self, reason: str, **kwargs: Any) -> None:
        self.reason = reason
        super().__init__(**kwargs)


class RobotsDisallowed(ToolError):
    """The site's robots.txt forbids this path.

    Separate from ``UrlRefused``: this is politeness, not security, and an
    operator may legitimately choose to override it for a specific domain.
    """

    status_code = 403
    code = "robots_disallowed"
    message = "That site asks not to be crawled at this path."


class FetchTimeout(ToolError):
    status_code = 504
    code = "fetch_timeout"
    message = "The source did not respond in time."
    retryable = True


class UpstreamUnavailable(ToolError):
    """A 5xx or a connection failure at the source."""

    status_code = 503
    code = "upstream_unavailable"
    message = "The source is temporarily unavailable."
    retryable = True


class UpstreamRateLimited(ToolError):
    status_code = 429
    code = "upstream_rate_limited"
    message = "The source is rate limiting this request."
    retryable = True

    def __init__(self, *args: Any, retry_after_seconds: float | None = None, **kwargs: Any) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(*args, **kwargs)


class UpstreamRejected(ToolError):
    """A 4xx that retrying will not fix: not found, gone, forbidden."""

    status_code = 502
    code = "upstream_rejected"
    message = "The source rejected this request."


class ResponseTooLarge(ToolError):
    """The response exceeded the size ceiling.

    Enforced while streaming, not after: a cap checked on a fully buffered body
    is not a cap, it is a report of how much memory was already spent.
    """

    status_code = 413
    code = "response_too_large"
    message = "That page is larger than this service will download."


class UnsupportedContentType(ToolError):
    """The response is not something this tool can read.

    Not an error in the source - a PDF is a perfectly good source - but it is
    not something ``fetch_url`` handles, and Phase 7's ingestion pipeline is
    where it belongs.
    """

    status_code = 415
    code = "unsupported_content_type"
    message = "That URL does not return readable text."


class ExtractionFailed(ToolError):
    """The page was fetched but no usable main content came out of it.

    Distinct from an empty page: a caller needs to tell "the site returned a
    JavaScript shell" from "the article really is that short".
    """

    status_code = 422
    code = "extraction_failed"
    message = "No readable content could be extracted from that page."


class SearchProviderNotConfigured(ToolError):
    """No API key for the configured search provider.

    A configuration defect surfaced at the point of use, naming the provider and
    the setting that would fix it.
    """

    status_code = 501
    code = "search_provider_not_configured"
    message = "No web search provider is configured."
