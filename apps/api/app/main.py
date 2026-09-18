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
from app.cache import build_cache
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import Database
from app.models import build_gateway
from app.observability.metrics import build_metrics
from app.observability.middleware import (
    REQUEST_ID_HEADER,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from app.observability.tracing import configure_tracing
from app.research.eventbus import build_event_broker
from app.sources import build_toolbelt
from app.storage import build_object_storage
from app.workers.queue import InMemoryJobQueue, JobQueue, RedisJobQueue, build_redis

logger = get_logger(__name__)

DESCRIPTION = """
Autonomous multi-agent research: decomposition, parallel retrieval, evidence
extraction, contradiction detection and citation-validated reports.

**This build is Phase 15 (caching).** A created run is queued here and executed
by a separate worker process, which ingests the documents it was created with,
runs the research graph, and records its claims, evidence, contradictions and
report. The worker streams its progress as it goes: `/events` relays what it
publishes over Redis, and every event is persisted, so a reconnect with
`Last-Event-ID` replays exactly. Searches, fetched pages and embeddings are
cached by content hash, and identical work in flight is done once. Endpoints
for capabilities that are not built return `not_implemented` rather than
fabricated content.
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
    # One cache per process, shared by the gateway and the toolbelt. The API
    # makes few cacheable calls itself; it is built here so that the two
    # process types are configured identically and a misconfiguration shows up
    # on whichever starts first (Phase 15).
    app.state.cache = build_cache(settings)
    # One gateway per process: it owns the concurrency semaphore, and a
    # per-request gateway would give each request its own, which is none.
    app.state.gateway = build_gateway(settings, cache=app.state.cache)
    # One toolbelt per process: it owns the guarded HTTP client, its connection
    # pool, the robots.txt cache and the concurrency semaphore. A per-request
    # belt would give each request its own of each, which is none of them.
    app.state.toolbelt = build_toolbelt(settings, cache=app.state.cache)
    # Redis fan-out plus the durable log, so an event published by a worker
    # reaches whichever replica is holding the stream, and a reconnect
    # replays from rows rather than from this process's memory (ADR 0006).
    app.state.broker = build_event_broker(settings, database=app.state.database)

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
        await app.state.gateway.close()
        await app.state.toolbelt.close()
        await app.state.cache.close()
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
    # Before the middleware, which is given them directly: a middleware that
    # reached for `app.state` per request would be doing a lookup on the hot
    # path for a value that never changes.
    app.state.metrics = build_metrics() if resolved.metrics_enabled else None
    configure_tracing(resolved)

    # Middleware runs bottom-up: the request id is established first so every
    # log line and error envelope inside the stack can carry it.
    app.add_middleware(SecurityHeadersMiddleware, is_production=resolved.is_production)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_allow_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        # Content-Disposition carries an upload's filename (POST /files).
        allow_headers=[
            "Content-Type",
            "Content-Disposition",
            "Authorization",
            "Last-Event-ID",
            "X-Aether-User",
        ],
        expose_headers=[REQUEST_ID_HEADER],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware, metrics=app.state.metrics)

    register_exception_handlers(app)
    app.include_router(probe_router)
    app.include_router(api_v1_router)

    return app


app = create_app()
