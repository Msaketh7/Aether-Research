"""A real Postgres for the test suite.

The schema is the product of this phase, so testing it against anything other
than Postgres would test the wrong thing: ``citext``, ``jsonb``, arrays,
generated ``tsvector`` columns, partial indexes and check constraints all behave
differently or not at all elsewhere.

Three ways to get a database, in order of preference:

1. ``AETHER_TEST_DATABASE_URL`` - what CI sets, pointing at a service container.
2. A throwaway cluster created with the locally installed ``initdb``/``pg_ctl``.
   It lives in a temporary directory on a non-default port with trust
   authentication, and is destroyed afterwards. It touches no existing cluster
   and needs no credentials.
3. Nothing - database tests skip with an explicit reason rather than silently
   passing.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

#: Where a Windows installer puts the binaries. Searched only if they are not
#: already on PATH.
WINDOWS_HINTS = (
    Path(r"C:\Program Files\PostgreSQL"),
    Path(r"C:\Program Files (x86)\PostgreSQL"),
)

TEST_DATABASE = "aether_test"


@dataclass(frozen=True)
class ProvisionedDatabase:
    """A database the suite may create and drop tables in.

    Not named ``TestDatabase``: pytest collects anything called ``Test*`` and
    would try to run the dataclass as a test class.
    """

    url: str
    #: Whether ``CREATE EXTENSION vector`` succeeds here. Not every Postgres
    #: build ships pgvector, and the tests that need it say so when it is
    #: missing instead of failing for an unrelated-looking reason.
    has_pgvector: bool
    #: How the database was obtained, for the skip message and the log.
    origin: str


def find_binary(name: str) -> Path | None:
    """Locate a Postgres binary on PATH, or under a standard install root."""
    if (found := shutil.which(name)) is not None:
        return Path(found)
    for root in WINDOWS_HINTS:
        if not root.is_dir():
            continue
        # Highest version first: 17 before 16.
        for version_dir in sorted(root.iterdir(), reverse=True):
            candidate = version_dir / "bin" / f"{name}.exe"
            if candidate.exists():
                return candidate
    return None


def free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run(command: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command to completion.

    ``capture`` must be off for ``pg_ctl start``: the server it spawns inherits
    the pipes and never closes them, so a capturing call waits forever for an
    EOF that only arrives when the database shuts down.
    """
    streams: dict[str, object] = (
        {"capture_output": True, "text": True}
        if capture
        else {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    )
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        command,
        check=False,
        timeout=180,
        **streams,  # type: ignore[arg-type]
    )


@contextmanager
def throwaway_cluster() -> Iterator[ProvisionedDatabase | None]:
    """Create, start, yield and destroy an isolated cluster."""
    initdb = find_binary("initdb")
    pg_ctl = find_binary("pg_ctl")
    if initdb is None or pg_ctl is None:
        yield None
        return

    workdir = Path(tempfile.mkdtemp(prefix="aether-pg-"))
    datadir = workdir / "data"
    logfile = workdir / "postgres.log"
    port = free_port()
    started = False

    try:
        created = _run(
            [
                str(initdb),
                "-D",
                str(datadir),
                "-U",
                "postgres",
                "-A",
                "trust",
                "-E",
                "UTF8",
                "--no-locale",
            ]
        )
        if created.returncode != 0:
            yield None
            return

        start = _run(
            [
                str(pg_ctl),
                "-D",
                str(datadir),
                "-l",
                str(logfile),
                "-w",
                "-t",
                "60",
                # Durability off: this cluster is thrown away, and fsync on
                # Windows makes the suite several times slower for no benefit.
                "-o",
                f"-p {port} -c listen_addresses=127.0.0.1 -c fsync=off "
                "-c synchronous_commit=off -c full_page_writes=off",
                "start",
            ],
            capture=False,
        )
        if start.returncode != 0:
            yield None
            return
        started = True

        base = f"postgresql://postgres@127.0.0.1:{port}"
        psql = find_binary("psql")
        if psql is not None:
            _run([str(psql), "-q", f"{base}/postgres", "-c", f'CREATE DATABASE "{TEST_DATABASE}"'])
            probe = _run(
                [
                    str(psql),
                    "-tAq",
                    f"{base}/{TEST_DATABASE}",
                    "-c",
                    "CREATE EXTENSION IF NOT EXISTS vector",
                ]
            )
            has_vector = probe.returncode == 0
        else:
            has_vector = False

        yield ProvisionedDatabase(
            url=f"postgresql+asyncpg://postgres@127.0.0.1:{port}/{TEST_DATABASE}",
            has_pgvector=has_vector,
            origin=f"throwaway cluster on port {port}",
        )
    finally:
        if started:
            _run(
                [str(pg_ctl), "-D", str(datadir), "-m", "immediate", "-w", "-t", "30", "stop"],
                capture=False,
            )
        shutil.rmtree(workdir, ignore_errors=True)


@contextmanager
def provision_database() -> Iterator[ProvisionedDatabase | None]:
    """Yield a usable test database, or ``None`` when none can be obtained."""
    configured = os.environ.get("AETHER_TEST_DATABASE_URL")
    if configured:
        yield ProvisionedDatabase(
            url=configured,
            # Assume the extension is present; CI runs the pgvector image. If
            # it is not, the migration fails loudly, which is the right answer.
            has_pgvector=os.environ.get("AETHER_TEST_PGVECTOR", "1") != "0",
            origin="AETHER_TEST_DATABASE_URL",
        )
        return

    with throwaway_cluster() as database:
        yield database


SKIP_REASON = (
    "No Postgres available. Set AETHER_TEST_DATABASE_URL, or install the "
    "PostgreSQL client tools so a throwaway cluster can be created."
)
