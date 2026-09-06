"""Request-scoped observability and security headers.

Phase 2 establishes the seams that Phase 17 fills with OpenTelemetry: a request
id that every log line carries, and one access-log record per request with the
fields an on-call engineer actually needs.
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

logger = get_logger("app.access")

REQUEST_ID_HEADER = "x-request-id"

Handler = Callable[[Request], Awaitable[Response]]


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, logs the request, and returns the id to the caller.

    The id is echoed in the response header and included in every error
    envelope, so a user can report "trace 9f3c…" and it can be found.
    """

    def __init__(self, app: ASGIApp, *, slow_request_ms: int = 1000) -> None:
        super().__init__(app)
        self._slow_request_ms = slow_request_ms

    async def dispatch(self, request: Request, call_next: Handler) -> Response:
        # Honour an upstream id so a trace survives a proxy hop.
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        request.state.request_id = request_id

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
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
