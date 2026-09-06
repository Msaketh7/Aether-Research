"""Run a command against a throwaway Postgres cluster.

Used to autogenerate and verify migrations without touching any existing
database and without needing credentials:

    uv run python scripts/with_test_db.py alembic upgrade head

The cluster is created, started, exposed as ``DATABASE_URL``, and destroyed
when the command exits.
"""

from __future__ import annotations

import os
import subprocess
import sys

from tests.support.postgres import provision_database


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: with_test_db.py <command> [args...]", file=sys.stderr)
        return 2

    with provision_database() as database:
        if database is None:
            print("No Postgres available; cannot run.", file=sys.stderr)
            return 3

        print(f"[with_test_db] {database.origin}", file=sys.stderr)
        print(f"[with_test_db] pgvector available: {database.has_pgvector}", file=sys.stderr)

        environment = dict(os.environ, DATABASE_URL=database.url, APP_ENV="test")
        completed = subprocess.run(argv, env=environment, check=False)  # noqa: S603
        return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
