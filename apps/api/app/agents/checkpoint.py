"""LangGraph's Postgres checkpointer, configured for this system (ADR 0014).

Three of the library's defaults are wrong here, and each is replaced for a
reason found by running it:

**Deserialization is allowlisted.** By default the serializer imports and
constructs whatever class a checkpoint names, with a warning. A checkpoint is a
row in a table, so anyone who can write that row can choose what code runs when
a worker loads it. The allowlist here is exactly the types a research state can
hold, derived from the state's own annotations rather than maintained by hand -
because a type left off it does not fail: it silently comes back as a ``dict``,
and the node that reads it fails after a resume, far from the cause. A test
round-trips every one of them through this serializer.

**The schema is Alembic's.** The library applies its own DDL from ``setup()``
and records a version in its own table. Migration 0005 applies the same
statements and records the same versions, so there is one migration history;
this module never calls ``setup()``. It checks the recorded version when the
checkpointer opens, and refuses to run against a schema older than the
installed library expects rather than failing on a worker's first write.

**psycopg, pooled and bounded.** The checkpointer is written against psycopg 3,
not the asyncpg the rest of the service uses, so it gets its own small pool,
with the same statement and acquisition timeouts as every other database call.
psycopg's async mode refuses Windows' default event loop; production runs on
Linux, and the test suite gives the tests that need it a selector loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import Enum
from typing import Annotated, Any, get_args, get_origin, get_type_hints

import psycopg
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel
from sqlalchemy.engine import make_url

from app.agents.errors import CheckpointSchemaOutdated
from app.agents.schemas import SubtaskAssignment
from app.agents.state import ResearchState
from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Everything a checkpoint can hold: the state, and the work orders of
#: researchers that had been dispatched but not finished when it was written.
_ROOTS: tuple[type, ...] = (ResearchState, SubtaskAssignment)


def checkpoint_types() -> frozenset[type]:
    """Every model and enum class reachable from what a checkpoint holds."""
    found: set[type] = set()
    for root in _ROOTS:
        if issubclass(root, BaseModel):
            _collect(root, found)
        else:
            for annotation in get_type_hints(root, include_extras=True).values():
                _collect(annotation, found)
    return frozenset(found)


def allowed_msgpack_modules() -> tuple[tuple[str, str], ...]:
    """The allowlist in the form the serializer takes: ``(module, class name)``."""
    return tuple(sorted((cls.__module__, cls.__name__) for cls in checkpoint_types()))


def build_serializer() -> JsonPlusSerializer:
    """The serializer every research checkpoint is written and read with."""
    return JsonPlusSerializer(
        allowed_msgpack_modules=allowed_msgpack_modules(),
        pickle_fallback=False,
    )


def psycopg_conninfo(database_url: str) -> str:
    """The service's SQLAlchemy URL, as psycopg takes it."""
    return make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)


@asynccontextmanager
async def open_checkpointer(settings: Settings) -> AsyncIterator[AsyncPostgresSaver]:
    """A checkpointer over a bounded pool, checked against the schema before use."""
    statement_timeout_ms = int(settings.db_command_timeout_seconds * 1000)
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        conninfo=psycopg_conninfo(settings.database_url),
        min_size=1,
        max_size=settings.checkpoint_pool_size,
        timeout=settings.db_pool_timeout_seconds,
        kwargs={
            # What the library's own connection factory sets: its statements
            # manage their own transactions, and prepared statements do not
            # survive a connection pooler.
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": f"-c statement_timeout={statement_timeout_ms}",
        },
        open=False,
        name="langgraph-checkpoints",
    )
    await pool.open(wait=True, timeout=settings.db_pool_timeout_seconds)
    try:
        await verify_checkpoint_schema(pool)
        yield AsyncPostgresSaver(pool, serde=build_serializer())
    finally:
        await pool.close()


async def verify_checkpoint_schema(pool: AsyncConnectionPool[Any]) -> None:
    """Refuse a schema older than the installed checkpointer; warn on a newer one.

    Newer is tolerated because a rolling deploy migrates the database before the
    last old worker has stopped, and the library's migrations are additive.
    """
    expected = len(AsyncPostgresSaver.MIGRATIONS) - 1
    async with pool.connection() as connection:
        try:
            cursor = await connection.execute("SELECT max(v) AS v FROM checkpoint_migrations")
            row = await cursor.fetchone()
        except psycopg.errors.UndefinedTable as exc:
            raise CheckpointSchemaOutdated(context={"current": None, "expected": expected}) from exc
    current = None if row is None else row["v"]
    if current is None or current < expected:
        raise CheckpointSchemaOutdated(context={"current": current, "expected": expected})
    if current > expected:
        logger.warning(
            "checkpoint schema is newer than the installed checkpointer",
            extra={"current": current, "expected": expected},
        )


def _collect(annotation: object, found: set[type]) -> None:
    origin = get_origin(annotation)
    if origin is Annotated:
        _collect(get_args(annotation)[0], found)
        return
    if origin is not None:
        for argument in get_args(annotation):
            _collect(argument, found)
        return
    if not isinstance(annotation, type) or annotation in found:
        return
    if issubclass(annotation, Enum):
        found.add(annotation)
    elif issubclass(annotation, BaseModel):
        found.add(annotation)
        for field in annotation.model_fields.values():
            _collect(field.annotation, found)
