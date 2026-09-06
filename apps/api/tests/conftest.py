"""Test fixtures.

Every test runs against a real ASGI application through a real HTTP client -
routing, dependency injection, middleware, validation and the exception
handlers all execute. Only the process boundaries (Postgres, Redis) are absent,
which is exactly what ``app_env="test"`` selects.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.principal import DEV_USER_HEADER, DEV_USER_ID
from app.core.config import Settings
from app.main import create_app

BASE_URL = "http://testserver"
API = "/api/v1"


@pytest.fixture
def settings() -> Settings:
    """Test configuration.

    The SSE timings are compressed so a stream test finishes in milliseconds
    instead of waiting on a 15-second production heartbeat.
    """
    return Settings(
        app_env="test",
        log_level="warning",
        sse_heartbeat_seconds=1,
        sse_max_connection_seconds=2,
        max_concurrent_runs_per_user=3,
        default_page_size=20,
        max_page_size=100,
    )


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """An HTTP client bound to the app, with lifespan startup and shutdown run."""
    app = create_app(settings)
    transport = ASGITransport(app=app)

    # ASGITransport does not run lifespan, so the object graph is assembled here
    # exactly as `lifespan` assembles it in a real process.
    async with (
        AsyncClient(transport=transport, base_url=BASE_URL) as http,
        app.router.lifespan_context(app),
    ):
        yield http


@pytest.fixture
async def tolerant_client(settings: Settings) -> AsyncIterator[AsyncClient]:
    """A client that returns the 500 response instead of re-raising.

    ASGITransport re-raises application exceptions by default, which is right
    for most tests. Asserting what a *client* sees when something unexpected
    breaks needs the opposite.
    """
    app = create_app(settings)
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with (
        AsyncClient(transport=transport, base_url=BASE_URL) as http,
        app.router.lifespan_context(app),
    ):
        yield http


@pytest.fixture
def other_user_id() -> UUID:
    """A second identity, for the cross-user authorisation tests."""
    return uuid4()


def as_user(user_id: UUID) -> dict[str, str]:
    """Headers that make a request act as a given user."""
    return {DEV_USER_HEADER: str(user_id)}


@pytest.fixture
def default_user_id() -> UUID:
    return DEV_USER_ID


def valid_request(**overrides: object) -> dict[str, object]:
    """A request body that passes validation, so a test can vary one field."""
    payload: dict[str, object] = {
        "question": "Compare the major AI inference infrastructure companies on pricing.",
        "mode": "deep",
        "depth": 3,
    }
    payload.update(overrides)
    return payload
