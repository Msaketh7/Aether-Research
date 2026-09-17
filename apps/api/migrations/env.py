"""Alembic environment.

Async-aware, and it takes the database URL from the application's typed
settings rather than from alembic.ini - so a migration can never run against a
different database than the service, and no connection string is committed.

"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Imported for the side effect: every model must be registered on Base.metadata
# before autogenerate compares it against the database, or the missing tables
# are silently treated as "already dropped".
import app.db.models  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base
from app.db.external import EXTERNALLY_OWNED_TABLES

config = context.config

if config.config_file_name is not None:
    # `disable_existing_loggers` defaults to True, which sets `disabled` on every
    # logger that already exists - including every `app.*` logger, since this
    # module imports the application to reach its metadata. Alembic's generated
    # env.py omits the argument, and the effect is silent: migrations log
    # normally and the application stops logging entirely. Harmless while
    # migrations run in their own process; not harmless in a worker that
    # migrates and then serves, and it is what made application logs
    # unassertable in the test suite (Phase 10).
    fileConfig(config.config_file_name, disable_existing_loggers=False)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def include_name(name: str | None, type_: str, parent_names: Mapping[str, str | None]) -> bool:
    """Leave tables Alembic creates but the ORM does not model out of autogenerate.

    Without this, every autogenerate would propose dropping LangGraph's
    checkpoint tables, because nothing in ``Base.metadata`` describes them. See
    ``app.db.external`` and ADR 0014.
    """
    if type_ == "table":
        return name not in EXTERNALLY_OWNED_TABLES
    return parent_names.get("table_name") not in EXTERNALLY_OWNED_TABLES


def run_migrations_offline() -> None:
    """Emit SQL without a connection, for review or for a DBA-run deploy."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Without these, a column type or server default change is silently
        # missed by autogenerate.
        compare_type=True,
        compare_server_default=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
