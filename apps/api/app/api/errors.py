"""Exception handlers.

Every error leaves the API in one shape - the ``ApiErrorBody`` envelope the
frontend already handles - and no error leaks an internal detail. Unexpected
exceptions are logged in full under the request id and reported to the client
as a generic message.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError
from app.core.logging import get_logger, log_context, request_id_var

logger = get_logger(__name__)


def error_body(
    *,
    code: str,
    message: str,
    details: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Build the wire envelope. One place, so the shape cannot drift."""
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    if (request_id := request_id_var.get()) is not None:
        error["trace_id"] = request_id
    return {"error": error}


def _field_errors(exc: RequestValidationError) -> dict[str, list[str]]:
    """Flatten Pydantic issues into ``{field: [messages]}``.

    The frontend renders these next to the input that caused them, so the key
    must be the field name the client sent, not Pydantic's full location tuple.
    """
    details: dict[str, list[str]] = {}
    for issue in exc.errors():
        location = [part for part in issue.get("loc", ()) if part not in ("body", "query", "path")]
        field = ".".join(str(part) for part in location) or "body"
        message = str(issue.get("msg", "Invalid value."))
        # Pydantic prefixes custom validator messages; the user does not need it.
        message = message.removeprefix("Value error, ")
        details.setdefault(field, []).append(message)
    return details


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        # Expected failures are logged at warning with their diagnostic context;
        # the context never reaches the client.
        logger.warning(
            "request rejected",
            extra={"code": exc.code, "status": exc.status_code, **log_context(exc.context)},
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code=exc.code, message=exc.message, details=exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = _field_errors(exc)
        logger.warning("request validation failed", extra={"fields": sorted(details)})
        return JSONResponse(
            status_code=422,
            content=error_body(
                code="validation_failed",
                message="Check the highlighted fields.",
                details=details,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Framework-raised errors (404 on an unknown path, 405) still leave in
        # the project envelope rather than Starlette's `{"detail": ...}`.
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(
                code=f"http_{exc.status_code}",
                message=str(exc.detail) if exc.detail else "Request failed.",
            ),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # The only place a stack trace is produced, and it goes to the log.
        logger.exception("unhandled exception", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            status_code=500,
            content=error_body(
                code="internal_error",
                message="Something went wrong. The incident has been logged.",
            ),
        )
