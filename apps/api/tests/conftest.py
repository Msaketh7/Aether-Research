"""Test fixtures.

Every test runs against a real ASGI application over a real HTTP client and a
real, migrated Postgres. Routing, dependency injection, middleware, validation,
the exception handlers, the session-per-request transaction and the SQL all
execute. Only Redis is substituted, by the in-memory queue that
``app_env="test"`` selects.

Using a real database is not thoroughness for its own sake: the schema is the
deliverable of this phase, and ``citext``, ``jsonb``, arrays, generated columns,
check constraints and keyset pagination behave differently or not at all
anywhere else.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient

from app.auth.principal import DEV_USER_HEADER, DEV_USER_ID
from app.core.config import Settings, get_settings
from app.db.base import Base
from app.main import create_app
from tests.support.postgres import SKIP_REASON, ProvisionedDatabase, provision_database
from tests.support.s3 import S3Server, run_s3_server

BASE_URL = "http://testserver"
API = "/api/v1"
API_ROOT = Path(__file__).resolve().parents[1]

#: The revision to migrate to when pgvector is unavailable. The core schema
#: stands on its own; only the embedding column needs the extension.
CORE_SCHEMA_REVISION = "0001_core_schema"


def _apply_migrations(database: ProvisionedDatabase) -> None:
    """Migrate the test database by running the real Alembic pipeline.

    Not ``Base.metadata.create_all``: that would test the models against
    themselves and prove nothing about the migrations, which are what actually
    build the schema in every other environment.
    """
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database.url
    get_settings.cache_clear()
    try:
        command.upgrade(config, "head" if database.has_pgvector else CORE_SCHEMA_REVISION)
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()


@pytest.fixture(scope="session")
def postgres() -> Iterator[ProvisionedDatabase | None]:
    """A migrated database for the whole session, or ``None`` if unobtainable."""
    with provision_database() as database:
        if database is not None:
            _apply_migrations(database)
        yield database


@pytest.fixture
def settings(postgres: ProvisionedDatabase | None, tmp_path: Path) -> Settings:
    """Test configuration.

    The SSE timings are compressed so a stream test finishes in milliseconds
    instead of waiting on a 15-second production heartbeat.

    Object storage gets a per-test directory. ``app_env="test"`` already selects
    the filesystem backend; naming the root explicitly is what keeps the suite
    from writing artifacts into the repository.
    """
    if postgres is None:
        pytest.skip(SKIP_REASON)
    return Settings(
        app_env="test",
        log_level="warning",
        database_url=postgres.url,
        storage_local_path=tmp_path / "object-storage",
        sse_heartbeat_seconds=1,
        sse_max_connection_seconds=2,
        max_concurrent_runs_per_user=3,
        default_page_size=20,
        max_page_size=100,
        # A small pool surfaces a leaked session as a timeout rather than as a
        # slow, mysterious suite.
        db_pool_size=5,
        db_max_overflow=2,
    )


#: Built once. The table list only changes when a model is added.
TRUNCATE_STATEMENT = (
    "TRUNCATE "
    + ", ".join(f'"{name}"' for name in Base.metadata.tables)
    + " RESTART IDENTITY CASCADE"
)


@pytest.fixture(autouse=True)
async def clean_database(postgres: ProvisionedDatabase | None) -> AsyncIterator[None]:
    """Empty every table between tests.

    TRUNCATE rather than recreating the schema: it is far faster, and CASCADE
    means the dependency order does not have to be maintained by hand as tables
    are added.

    A bare asyncpg connection rather than a SQLAlchemy engine: building a pool
    for one statement, once per test, was measurably the most expensive thing
    the suite did.
    """
    yield
    if postgres is None:
        return

    connection = await asyncpg.connect(postgres.url.replace("+asyncpg", ""))
    try:
        await connection.execute(TRUNCATE_STATEMENT)
    finally:
        await connection.close()


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


@pytest.fixture(scope="session")
def s3_server() -> Iterator[S3Server]:
    """A real S3 server for the storage tests, started on first use.

    Session-scoped because starting it costs more than every test that uses it
    put together; isolation comes from a fresh bucket per test instead.
    """
    with run_s3_server() as server:
        yield server


def s3_settings(server: S3Server, *, bucket: str, **overrides: object) -> Settings:
    """Configuration pointing the S3 backend at the test server.

    Every field is overridable, including the region, so a test can exercise a
    configuration a developer could plausibly write.
    """
    fields: dict[str, object] = {
        "app_env": "test",
        "log_level": "warning",
        "storage_backend": "s3",
        "s3_endpoint_url": server.endpoint_url,
        "s3_bucket": bucket,
        "s3_region": server.region,
        "s3_access_key_id": server.access_key_id,
        "s3_secret_access_key": server.secret_access_key,
    }
    fields.update(overrides)
    return Settings(**fields)


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
