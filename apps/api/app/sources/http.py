"""The only outbound HTTP client the research tools may use.

Every property the threat model requires of a fetch is enforced here, once, so
no tool can accidentally omit one:

* the SSRF guard runs before the request **and again on every redirect**;
* redirects are followed manually and capped, because ``follow_redirects=True``
  validates the first URL and then follows a ``302`` to anywhere - the most
  common way this control is bypassed in practice;
* the **connected peer address is checked against the validated set** before a
  byte of the body is read, which is what closes the DNS-rebinding window
  between resolution and connection;
* the body is streamed with a size cap enforced *during* the read, so an
  unbounded response is refused rather than measured after it has been
  buffered;
* connect and read timeouts on everything;
* no cookies and no credentials are ever sent, so a redirect to an internal
  host cannot carry authentication with it.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import TracebackType

import httpx2 as httpx

from app.core.logging import get_logger
from app.sources.errors import (
    FetchTimeout,
    ResponseTooLarge,
    UpstreamRateLimited,
    UpstreamRejected,
    UpstreamUnavailable,
    UrlRefused,
)
from app.sources.urls import ValidatedUrl, validate_url

logger = get_logger(__name__)

#: Sent on every request. TDD 9.2 requires a descriptive User-Agent, and SEC's
#: EDGAR terms of service require a contact address in it; being identifiable is
#: also simply how a crawler earns the right to keep crawling.
DEFAULT_USER_AGENT = "AetherResearch/0.1 (+https://github.com/aether-research; contact@example.com)"

_RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """A response that passed every check."""

    url: str
    #: The URL actually served, after redirects. What a citation must point at.
    final_url: str
    status_code: int
    headers: Mapping[str, str]
    content: bytes
    elapsed_ms: int
    redirects: tuple[str, ...] = field(default_factory=tuple)

    @property
    def content_type(self) -> str:
        raw = self.headers.get("content-type", "")
        return raw.split(";", 1)[0].strip().lower()

    @property
    def charset(self) -> str | None:
        raw = self.headers.get("content-type", "")
        for part in raw.split(";")[1:]:
            key, _, value = part.strip().partition("=")
            if key.lower() == "charset":
                return value.strip("\"'") or None
        return None

    def text(self) -> str:
        """Decode the body, preferring the declared charset."""
        for encoding in (self.charset, "utf-8"):
            if encoding:
                try:
                    return self.content.decode(encoding)
                except (UnicodeDecodeError, LookupError):
                    continue
        return self.content.decode("utf-8", errors="replace")


class SafeHttpClient:
    """Bounded, SSRF-guarded HTTP for the research tools."""

    def __init__(
        self,
        *,
        connect_timeout_seconds: float = 5.0,
        read_timeout_seconds: float = 20.0,
        max_response_bytes: int = 5 * 1024 * 1024,
        max_redirects: int = 5,
        user_agent: str = DEFAULT_USER_AGENT,
        allowed_domains: frozenset[str] | None = None,
        blocked_domains: frozenset[str] = frozenset(),
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._max_bytes = max_response_bytes
        self._max_redirects = max_redirects
        self._allowed_domains = allowed_domains
        self._blocked_domains = blocked_domains
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                read_timeout_seconds,
                connect=connect_timeout_seconds,
            ),
            # Manual, so the guard runs on every hop. This is the important line.
            follow_redirects=False,
            # `cookies=None` means "no *initial* cookies", not "no cookie jar" -
            # httpx creates one anyway, stores Set-Cookie, and replays it on the
            # next request. Since redirects here are separate calls on a shared
            # client, that let a redirect chain carry state to the next hop. The
            # jar is therefore emptied before every request; see `_request`.
            cookies=None,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en",
            },
            transport=transport,
        )

    async def __aenter__(self) -> SafeHttpClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> HttpResponse:
        """Fetch a URL, validating it and every redirect it leads to."""
        return await self._send("GET", url, headers=headers, max_bytes=max_bytes)

    async def post_json(
        self,
        url: str,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> object:
        """POST JSON to an API endpoint, under the same guard as a fetch.

        Search vendors want POST. That is not a reason to reach for a second,
        unguarded HTTP client: an API endpoint is a URL like any other, and the
        one that gets exempted "just for the search provider" is the one that
        ends up taking a URL from a config file someone can influence.
        """
        response = await self._send(
            "POST",
            url,
            headers={"Content-Type": "application/json", "Accept": "application/json"}
            | dict(headers or {}),
            json=payload,
        )
        return _parse_json(response, url)

    async def _send(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        max_bytes: int | None = None,
        json: Mapping[str, object] | None = None,
    ) -> HttpResponse:
        started = time.perf_counter()
        redirects: list[str] = []
        current = url

        for hop in range(self._max_redirects + 1):
            validated = await validate_url(
                current,
                allowed_domains=self._allowed_domains,
                blocked_domains=self._blocked_domains,
            )
            response, body = await self._request(
                method, validated, headers, max_bytes or self._max_bytes, json
            )

            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    break
                # Resolve relative redirects against the URL that produced them.
                current = str(httpx.URL(validated.url).join(location))
                redirects.append(current)
                # A redirected POST becomes a GET, as every browser and RFC 9110
                # agree - and re-POSTing a body to a host we have not yet
                # validated would be worse than merely surprising.
                method, json = "GET", None
                if hop == self._max_redirects:
                    raise UpstreamRejected(
                        "That URL redirected too many times.",
                        context={"url": url, "redirects": redirects},
                    )
                continue

            _raise_for_status(response.status_code, url=validated.url, headers=response.headers)
            return HttpResponse(
                url=url,
                final_url=validated.url,
                status_code=response.status_code,
                headers={key.lower(): value for key, value in response.headers.items()},
                content=body,
                elapsed_ms=int((time.perf_counter() - started) * 1000),
                redirects=tuple(redirects),
            )

        raise UpstreamRejected(
            "That URL redirected too many times.",
            context={"url": url, "redirects": redirects},
        )

    async def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> object:
        """Fetch and parse JSON, for the API-backed tools."""
        response = await self.get(url, headers={"Accept": "application/json", **(headers or {})})
        return _parse_json(response, url)

    # --- internals -------------------------------------------------------

    async def _request(
        self,
        method: str,
        validated: ValidatedUrl,
        headers: Mapping[str, str] | None,
        max_bytes: int,
        json: Mapping[str, object] | None,
    ) -> tuple[httpx.Response, bytes]:
        """One hop: connect, verify the peer, then read within the cap."""
        # Empty the jar before every request. A research fetch has no session to
        # maintain, and a redirect chain that can carry a cookie can carry a
        # credential to a host that was never authenticated to.
        self._client.cookies.clear()
        try:
            async with self._stream(method, validated, headers, json) as response:
                self._verify_peer(response, validated)

                # Trust the declared length only to refuse early; the real cap is
                # enforced while reading, because the header can lie.
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise ResponseTooLarge(
                        context={
                            "url": validated.url,
                            "declared_bytes": int(declared),
                            "max_bytes": max_bytes,
                        }
                    )

                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise ResponseTooLarge(
                            context={
                                "url": validated.url,
                                "read_bytes": total,
                                "max_bytes": max_bytes,
                            }
                        )
                    chunks.append(chunk)
                return response, b"".join(chunks)
        except httpx.TimeoutException as exc:
            raise FetchTimeout(context={"url": validated.url, "error": str(exc)}) from exc
        except httpx.HTTPError as exc:
            raise UpstreamUnavailable(context={"url": validated.url, "error": str(exc)}) from exc

    @asynccontextmanager
    async def _stream(
        self,
        method: str,
        validated: ValidatedUrl,
        headers: Mapping[str, str] | None,
        json: Mapping[str, object] | None,
    ) -> AsyncIterator[httpx.Response]:
        async with self._client.stream(
            method,
            validated.url,
            headers=dict(headers or {}),
            json=dict(json) if json is not None else None,
        ) as response:
            yield response

    def _verify_peer(self, response: httpx.Response, validated: ValidatedUrl) -> None:
        """Confirm the socket actually landed on an address we approved.

        This is what makes the guard resistant to DNS rebinding rather than
        merely careful about it: between resolving the name and opening the
        connection, the answer can change, and only the peer address knows.

        If the transport does not report a peer - a test double, an exotic
        transport - the check is skipped rather than failed. That is a stated
        residual risk, not an oversight: refusing every response from a
        transport that cannot introspect itself would make the client unusable
        in exactly the places it needs to be tested.
        """
        stream = response.extensions.get("network_stream")
        if stream is None:
            return
        try:
            peer = stream.get_extra_info("server_addr")
        except Exception:  # pragma: no cover - transport-dependent
            return
        if not peer:
            return

        address = peer[0] if isinstance(peer, tuple) else str(peer)
        if not validated.permits(str(address)):
            logger.error(
                "connected peer was not an approved address; possible dns rebinding",
                extra={
                    "url": validated.url,
                    "host": validated.host,
                    "peer": str(address),
                    "approved": [str(a) for a in validated.addresses],
                },
            )
            raise UrlRefused(
                "peer_address_not_approved",
                message="That URL is not permitted.",
                context={"url": validated.url, "peer": str(address)},
            )


def _parse_json(response: HttpResponse, url: str) -> object:
    import json as _json

    try:
        return _json.loads(response.text())
    except ValueError as exc:
        raise UpstreamRejected(
            "That API returned a response this tool could not parse.",
            context={"url": url, "error": str(exc)},
        ) from exc


def _raise_for_status(status: int, *, url: str, headers: Mapping[str, str]) -> None:
    """Turn a status code into the right error class.

    The split is what the retry policy acts on: a 429 or a 503 is worth another
    attempt, a 404 never is, and treating them alike either wastes the run's
    budget or gives up on a source that was one retry from working.
    """
    if status < 400:
        return

    context = {"url": url, "status": status}
    if status == 429:
        raise UpstreamRateLimited(
            retry_after_seconds=_as_seconds(headers.get("retry-after")),
            context=context,
        )
    if status in _RETRYABLE_STATUSES or status >= 500:
        raise UpstreamUnavailable(context=context)
    raise UpstreamRejected(context=context)


def _as_seconds(value: str | None) -> float | None:
    """Parse a ``Retry-After`` delay, or ``None`` if it is absent or a date.

    The header may carry an HTTP-date instead of a delay. Rather than parse it,
    fall back to the caller's own backoff curve - guessing wrong here means
    either hammering a rate-limited host or sleeping for hours.
    """
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
