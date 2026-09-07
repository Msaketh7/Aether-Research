"""Application factory and process wiring.

One place assembles the object graph: settings, logging, database, queue, event
broker and the research service. Routes receive them through
``app/api/deps.py`` and never construct anything themselves, which is what lets
Phase 3 replace the repository and Phase 14 replace the broker without touching
an endpoint.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_exception_handlers
from app.api.router import api_v1_router, probe_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import Database
from app.observability.middleware import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.research.events import InMemoryEventBroker
from app.storage import build_object_storage
from app.workers.queue import InMemoryJobQueue, JobQueue, RedisJobQueue, build_redis

logger = get_logger(__name__)

DESCRIPTION = """
Autonomous multi-agent research: decomposition, parallel retrieval, evidence
extraction, contradiction detection and citation-validated reports.

**This build is Phase 3 (data layer).** The API surface, validation,
authorisation, error contract, progress stream and Postgres persistence are
real. There is no worker consuming the queue yet, so a created run stays
`queued`, and endpoints for data a run has not produced return empty results or
`not_implemented` rather than fabricated content.
""".strip()


def _build_queue(settings: Settings) -> JobQueue:
    """Redis everywhere except tests.

    The in-memory queue is never selected implicitly: a misconfigured
    deployment must fail its readiness probe, not silently drop research jobs.
    """
    if settings.app_env == "test":
        return InMemoryJobQueue()
    return RedisJobQueue(build_redis(settings.redis_url))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings

    # Process-lifetime objects only. Repositories and the research service are
    # request-scoped, because each needs the session that is that request's
    # transaction (see app/api/deps.py).
    app.state.database = Database(settings)
    app.state.queue = _build_queue(settings)
    app.state.storage = build_object_storage(settings)
    app.state.broker = InMemoryEventBroker(buffer_size=settings.sse_replay_buffer_size)

    logger.info(
        "api starting",
        extra={
            "environment": settings.app_env,
            "queue": type(app.state.queue).__name__,
            "storage": type(app.state.storage).__name__,
        },
    )
    try:
        yield
    finally:
        # Ordered shutdown: stop accepting work, then release connections.
        await app.state.queue.close()
        await app.state.broker.close()
        await app.state.storage.close()
        await app.state.database.dispose()
        logger.info("api stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI application.

    Takes settings so tests can construct an app with a different environment
    without mutating process state.
    """
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    app = FastAPI(
        title="Aether Research API",
        description=DESCRIPTION,
        version="0.1.0",
        docs_url=resolved.docs_url,
        redoc_url=None,
        openapi_url=None if resolved.is_production else "/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = resolved

    # Middleware runs bottom-up: the request id is established first so every
    # log line and error envelope inside the stack can carry it.
    app.add_middleware(SecurityHeadersMiddleware, is_production=resolved.is_production)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "Last-Event-ID", "X-Aether-User"],
        expose_headers=[REQUEST_ID_HEADER],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    app.include_router(probe_router)
    app.include_router(api_v1_router)

    return app


app = create_app()
