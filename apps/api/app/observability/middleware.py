"""Request-scoped observability and security headers.

Phase 2 established the seams; Phase 17 fills them. One request now produces a
request id that every log line carries, one access-log record, one counter and
one histogram observation, and one span - all from the same measurement, so
they cannot disagree about how long it took.

**The metric is labelled by route template, never by URL.**
`/api/v1/research/{run_id}` is one time series; `/api/v1/research/9f3c…` would
be one per run, which is how a metrics system is taken down by its own
instrumentation. The template is rebuilt by substituting the captured path
parameters back into the path (``route_of``), and a request that matched
nothing is labelled ``unmatched`` rather than by its path - an unmatched path
is attacker-controlled, and an attacker-controlled label value is the same
cardinality problem with somebody behind it.

**A stream is measured as started, not as slow.** An SSE connection is held
open for minutes by design; recording that as request latency would make the
p99 a measurement of how long people watch their runs.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.logging import get_logger, request_id_var
from app.observability.metrics import Metrics
from app.observability.tracing import span

logger = get_logger("app.access")

REQUEST_ID_HEADER = "x-request-id"

#: What a request that matched no route is labelled as.
UNMATCHED_ROUTE = "unmatched"

Handler = Callable[[Request], Awaitable[Response]]


def route_of(request: Request) -> str:
    """The matched route's full template, or ``unmatched``.

    Built by substituting each captured path parameter back into the request's
    own path, rather than read off the matched route. The route object carries
    only its *router-relative* path - this FastAPI mounts its routers rather
    than flattening them - so ``/api/v1/research/{run_id}`` and a future
    ``/api/v2/research/{run_id}`` would arrive here as the same string and
    merge into one time series.

    The substitution also makes the safety property structural rather than
    incidental: every captured value is replaced, so a label can only contain
    a run id if the router never captured it - and a path the router did not
    match is reported as ``unmatched`` without being read at all.
    """
    if request.scope.get("route") is None:
        return UNMATCHED_ROUTE
    template = request.url.path
    for name, value in (request.scope.get("path_params") or {}).items():
        template = template.replace(str(value), "{" + name + "}")
    return template


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, measures the request, and returns the id to the caller.

    The id is echoed in the response header and included in every error
    envelope, so a user can report "trace 9f3c…" and it can be found.
    """

    def __init__(
        self, app: ASGIApp, *, slow_request_ms: int = 1000, metrics: Metrics | None = None
    ) -> None:
        super().__init__(app)
        self._slow_request_ms = slow_request_ms
        self._metrics = metrics

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        # Honour an upstream id so a trace survives a proxy hop.
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            with span(
                request.method,
                **{
                    "http.request.method": request.method,
                    "url.path": request.url.path,
                    "aether.request_id": request_id,
                },
            ):
                response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            self._observe(request, status=500, seconds=duration_ms / 1000)
            logger.exception(
                "request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round(duration_ms, 2),
                },
            )
            raise
        finally:
            request_id_var.reset(token)

        duration_ms = (time.perf_counter() - started) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id

        # Streaming responses have no meaningful duration at this point; they
        # are logged as started rather than as slow.
        is_stream = response.headers.get("content-type", "").startswith("text/event-stream")
        self._observe(
            request,
            status=response.status_code,
            seconds=None if is_stream else duration_ms / 1000,
        )
        logger.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": None if is_stream else round(duration_ms, 2),
                "stream": is_stream,
                "slow": (not is_stream) and duration_ms > self._slow_request_ms,
            },
        )
        return response

    def _observe(self, request: Request, *, status: int, seconds: float | None) -> None:
        """Count the request, and time it unless timing it would be a lie.

        Wrapped, like every other observation in this system: a request that
        failed because a counter did would be an outage caused by watching
        for one.
        """
        if self._metrics is None:
            return
        try:
            route = route_of(request)
            self._metrics.http_requests.labels(
                method=request.method, route=route, status=str(status)
            ).inc()
            if seconds is not None:
                self._metrics.http_duration.labels(method=request.method, route=route).observe(
                    seconds
                )
        except Exception:
            logger.debug("a request metric could not be recorded")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Baseline response headers.

    Set here rather than at a proxy so they apply in development too - a header
    that only exists in production is a header nobody tests.
    """

    def __init__(self, app: ASGIApp, *, is_production: bool) -> None:
        super().__init__(app)
        self._is_production = is_production

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # The API serves JSON and event streams, never markup or scripts.
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
        )
        if self._is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
