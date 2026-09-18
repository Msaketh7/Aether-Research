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

**Under xdist every worker gets its own database.** Each worker is a separate
process, so the throwaway path already isolates them - a cluster each. The
configured-URL path does not, and sharing one database would be silently wrong
rather than merely slow: the suite truncates every table between tests, so two
workers would delete each other's rows mid-test. A worker therefore appends its
own id to the database name and creates it.

**A cluster is destroyed even when the run is killed.** The context manager's
``finally`` only runs on a clean exit, and a suite interrupted by a timeout used
to leave its cluster behind - twenty of them, 1.4 GB, accumulated in a single
session. The stop is also registered with ``atexit``, and any directory left by
an earlier crash is swept on the next start.
"""

from __future__ import annotations

import atexit
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

#: How long a leftover cluster directory must have gone untouched before a new
#: run treats it as abandoned and removes it. Comfortably longer than any single
#: suite, so a run in another terminal is never swept out from under itself.
ABANDONED_AFTER_SECONDS = 6 * 60 * 60

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


def xdist_worker() -> str | None:
    """This process's xdist worker id (``gw0``), or ``None`` when not parallel."""
    return os.environ.get("PYTEST_XDIST_WORKER")


OWNER_FILE = "owner.pid"


def process_alive(pid: int) -> bool:
    """Whether ``pid`` is a running process, without signalling it.

    ``os.kill(pid, 0)`` is the usual trick and is actively dangerous here: on
    Windows Python implements ``os.kill`` as ``TerminateProcess``, so the probe
    would kill the process it was asking about.
    """
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # it exists, it is simply not ours
    return True


def cluster_owner(workdir: Path) -> int | None:
    """The pid of the test process that created this cluster, if recorded."""
    try:
        return int((workdir / OWNER_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def sweep_abandoned_clusters() -> None:
    """Stop and remove clusters whose test process is gone.

    ``atexit`` covers an ordinary failure and a Ctrl-C, but not the way this
    suite is usually interrupted here - a hard kill on a timeout - which runs no
    Python at all. Worse, the server survives its killer: ``pg_ctl`` starts a
    detached postmaster, so killing pytest leaves a *running* cluster, which is
    how twenty of them and 1.4 GB accumulated in a single session.

    So each run clears up after the ones before it, and the test is whether the
    process that created a cluster is still alive - not whether the cluster is,
    because it always is. A directory with no owner recorded falls back to age,
    which is what the clusters from before this existed look like.
    """
    root = Path(tempfile.gettempdir())
    pg_ctl = find_binary("pg_ctl")
    cutoff = time.time() - ABANDONED_AFTER_SECONDS
    for candidate in root.glob("aether-pg-*"):
        try:
            if not candidate.is_dir():
                continue
            owner = cluster_owner(candidate)
            if owner is None:
                if candidate.stat().st_mtime > cutoff:
                    continue
            elif process_alive(owner):
                continue
            if pg_ctl is not None:
                _run(
                    [
                        str(pg_ctl),
                        "-D",
                        str(candidate / "data"),
                        "-m",
                        "immediate",
                        "-w",
                        "-t",
                        "20",
                        "stop",
                    ],
                    capture=False,
                )
            shutil.rmtree(candidate, ignore_errors=True)
        except OSError:
            continue


def per_worker_url(url: str, worker: str) -> str:
    """The same server, a database of this worker's own.

    Truncating every table between tests is what keeps the suite isolated, and
    it is exactly what makes a shared database unusable in parallel: one worker's
    cleanup deletes another's fixtures mid-test. Suffixing the name is cheaper
    and far more obvious than trying to make the truncation worker-aware.
    """
    base, _, database = url.rpartition("/")
    return f"{base}/{database}_{worker}"


def ensure_database(url: str) -> None:
    """Create the database named by ``url`` if it is not there.

    asyncpg rather than ``psql``: the client binaries are not necessarily
    installed next to a service container, and asyncpg is already a dependency.
    It does not speak SQLAlchemy's ``postgresql+asyncpg://`` though - that
    scheme belongs to the engine, not the driver - so the prefix comes off
    first. It did not, once, and the result was every worker skipping every
    database test while the run stayed green.

    Raises rather than returning a flag. A configured database that cannot be
    reached has to stop the run: skipping instead is how a suite passes without
    testing anything.
    """
    import asyncio

    import asyncpg

    base, _, database = url.rpartition("/")
    dsn = f"{base}/postgres".replace("postgresql+asyncpg://", "postgresql://")

    async def create() -> None:
        connection = await asyncpg.connect(dsn)
        try:
            await connection.execute(f'CREATE DATABASE "{database}"')
        except asyncpg.DuplicateDatabaseError:
            pass
        finally:
            await connection.close()

    asyncio.run(create())


@contextmanager
def throwaway_cluster() -> Iterator[ProvisionedDatabase | None]:
    """Create, start, yield and destroy an isolated cluster."""
    initdb = find_binary("initdb")
    pg_ctl = find_binary("pg_ctl")
    if initdb is None or pg_ctl is None:
        yield None
        return

    sweep_abandoned_clusters()

    workdir = Path(tempfile.mkdtemp(prefix="aether-pg-"))
    # Written before the server starts, so a crash at any point after this
    # leaves a directory the next run can identify as abandoned.
    (workdir / OWNER_FILE).write_text(str(os.getpid()), encoding="utf-8")
    datadir = workdir / "data"
    logfile = workdir / "postgres.log"
    port = free_port()
    started = False

    def shutdown() -> None:
        """Stop the server and remove its directory. Safe to call twice."""
        if not workdir.exists():
            return
        _run(
            [str(pg_ctl), "-D", str(datadir), "-m", "immediate", "-w", "-t", "30", "stop"],
            capture=False,
        )
        shutil.rmtree(workdir, ignore_errors=True)

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
        # The `finally` below runs only on a clean exit. A suite killed by a
        # timeout - which is how this one is usually interrupted here - skips it
        # and leaks the cluster, so the stop is registered with the interpreter
        # as well. `atexit` does not run on SIGKILL either; that is what the
        # sweep at the top of this function is for.
        atexit.register(shutdown)

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
            shutdown()
            atexit.unregister(shutdown)
        else:
            shutil.rmtree(workdir, ignore_errors=True)


@contextmanager
def provision_database() -> Iterator[ProvisionedDatabase | None]:
    """Yield a usable test database, or ``None`` when none can be obtained."""
    configured = os.environ.get("AETHER_TEST_DATABASE_URL")
    if configured:
        url, origin = configured, "AETHER_TEST_DATABASE_URL"
        if (worker := xdist_worker()) is not None:
            # One database per worker on the same server; see per_worker_url.
            url = per_worker_url(configured, worker)
            origin = f"AETHER_TEST_DATABASE_URL ({worker})"
            ensure_database(url)
        yield ProvisionedDatabase(
            url=url,
            # Assume the extension is present; CI runs the pgvector image. If
            # it is not, the migration fails loudly, which is the right answer.
            has_pgvector=os.environ.get("AETHER_TEST_PGVECTOR", "1") != "0",
            origin=origin,
        )
        return

    with throwaway_cluster() as database:
        yield database


SKIP_REASON = (
    "No Postgres available. Set AETHER_TEST_DATABASE_URL, or install the "
    "PostgreSQL client tools so a throwaway cluster can be created."
)


#: What to migrate to when pgvector is unavailable: the head of the relational
#: line. Migrations form two branches (see 0003_document_ingestion); only the
#: ``vector`` line needs the extension.
CORE_ONLY = "core@head"


def apply_migrations(database_url: str, *, with_vector: bool) -> None:
    """Build the schema the way every other environment builds it.

    A provisioned cluster has no schema at all, so a script pointed at one has
    to migrate before it can do anything - and it has to migrate rather than
    call ``Base.metadata.create_all``, which would test the models against
    themselves and prove nothing about the migrations that build the real
    thing.

    Alembic's ``env.py`` calls ``asyncio.run`` itself, so a caller inside a
    running loop must hand this to a thread.
    """
    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "heads" if with_vector else CORE_ONLY)
