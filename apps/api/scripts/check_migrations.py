"""Verify the migrations are reversible and idempotent.

Runs upgrade -> downgrade -> upgrade against a throwaway cluster and asserts the
table set is identical at both upgrade points. A migration that cannot be rolled
back is a deployment with no way out, so this runs in CI as well as by hand:

    uv run python scripts/check_migrations.py
"""

from __future__ import annotations

import asyncio
import os
import sys

import asyncpg
from alembic import command
from alembic.config import Config
from tests.support.postgres import provision_database

from app.core.config import get_settings

TABLES_QUERY = (
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
    "ORDER BY table_name"
)


async def table_names(dsn: str) -> list[str]:
    connection = await asyncpg.connect(dsn)
    try:
        return [row["table_name"] for row in await connection.fetch(TABLES_QUERY)]
    finally:
        await connection.close()


def main() -> int:
    with provision_database() as database:
        if database is None:
            print("No Postgres available; cannot verify migrations.", file=sys.stderr)
            return 3

        target = "head" if database.has_pgvector else "0001_core_schema"
        if target != "head":
            print(
                "pgvector is unavailable here, so 0002_pgvector_embeddings is not "
                "exercised. Run against the pgvector/pgvector image to cover it.",
                file=sys.stderr,
            )

        os.environ["DATABASE_URL"] = database.url
        get_settings.cache_clear()

        config = Config("alembic.ini")
        dsn = database.url.replace("+asyncpg", "")

        command.upgrade(config, target)
        first = asyncio.run(table_names(dsn))

        command.downgrade(config, "base")
        after_downgrade = asyncio.run(table_names(dsn))

        command.upgrade(config, target)
        second = asyncio.run(table_names(dsn))

    # `alembic_version` is Alembic's own bookkeeping and survives a downgrade.
    remaining = [name for name in after_downgrade if name != "alembic_version"]

    problems: list[str] = []
    if remaining:
        problems.append(f"downgrade left tables behind: {remaining}")
    if first != second:
        problems.append(f"re-upgrade produced a different schema: {set(first) ^ set(second)}")
    if len(first) < 19:
        problems.append(f"expected at least 19 tables, found {len(first)}")

    for problem in problems:
        print(f"FAIL: {problem}", file=sys.stderr)
    if problems:
        return 1

    print(f"OK: {len(first)} tables, upgrade/downgrade/upgrade round trip is clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
