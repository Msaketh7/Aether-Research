"""Run the whole application locally, with one command.

    make start

Four things have to be true before this product does anything interesting: the
database is migrated, the API is up, a worker is consuming runs, and the
frontend is pointed at the API rather than at its fixtures. Starting those by
hand is four terminals and a specific order, and getting the order wrong fails
in ways that look like bugs - a frontend in mock mode looks like a working app
that ignores your backend, and a missing worker looks like a run that queues and
never starts.

So this does the four, in order, and says which of them is not ready yet.

**It adapts to what is installed rather than demanding it.** Redis and pgvector
are both optional here and both change what the system can do, so each is
detected and the consequence is printed rather than discovered:

  * **No Redis** - the API and the worker cannot share a queue, so the worker
    falls back on the reconciliation sweep, which finds queued runs in Postgres
    itself. That is a real mechanism rather than a test stub (see
    ``app/workers/loop.py``); it just costs a delay before a run starts, and
    this lowers that delay from a deployment's minute to a few seconds.
  * **No pgvector** - only the relational migration branch is applied, and
    retrieval runs its lexical arm. Chunks are stored with their vectors
    pending, which is a documented state and not an error (ADR 0013).
  * **No usable database** - it creates one, under `.data/postgres`, with
    `initdb` and trust auth on port 55432. This is the difference between a
    first run that works and one that stops on a superuser password: both
    `createuser` and `createdb` against an existing service need one, and a
    PostgreSQL installed once with its password long forgotten is a wall rather
    than a step. A cluster we create has no such wall, and the data persists, so
    it is a real development database rather than a throwaway.

    A `DATABASE_URL` you set explicitly is never second-guessed: a launcher
    quietly using a different database than the one you configured is the worst
    thing it could do. It is only the *default* that falls back.

Stdlib only, and no Docker: the point is to be runnable on a machine that has
Postgres installed and nothing else set up.

Ctrl-C stops all three, children included.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parent.parent
API = REPO / "apps" / "api"
WEB = REPO / "apps" / "web"

API_PORT = 8000
WEB_PORT = 3000

#: Prefix colours, so three interleaved logs can be told apart at a glance.
COLOURS = {"api": "\033[36m", "worker": "\033[35m", "web": "\033[32m"}
DIM = "\033[2m"
BOLD = "\033[1m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"

IS_WINDOWS = sys.platform == "win32"

#: Output stays ASCII. A Windows console is cp1252 by default, and a
#: box-drawing character in a log prefix raises UnicodeEncodeError rather
#: than printing badly - which turned this launcher into a traceback on its
#: very first line the first time it ran.


def colour(text: str, code: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"{code}{text}{RESET}"


def say(message: str) -> None:
    print(f"{colour('==>', BOLD)} {message}", flush=True)


def warn(message: str) -> None:
    print(f"{colour('!', YELLOW)} {message}", flush=True)


def fail(message: str, *, fix: str | None = None) -> None:
    print(f"\n{colour('ERR', RED)} {message}", file=sys.stderr)
    if fix:
        print(f"  {colour('try:', DIM)} {fix}", file=sys.stderr)
    sys.exit(1)


# --- environment -------------------------------------------------------------


def read_dotenv() -> dict[str, str]:
    """`.env` if it exists. Not exported over anything already in the shell."""
    path = REPO / ".env"
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def database_url(env: dict[str, str]) -> str:
    return (
        os.environ.get("DATABASE_URL")
        or env.get("DATABASE_URL")
        or "postgresql+asyncpg://aether:aether@localhost:5432/aether"
    )


def listening(host: str, port: int, *, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def port_owner(port: int) -> bool:
    """Whether something already holds a port we are about to bind."""
    return listening("127.0.0.1", port, timeout=0.3)


# --- a database of our own ----------------------------------------------------

#: Where the managed cluster lives. Under `.data/`, which is already ignored,
#: and persistent: a developer's runs, sources and reports should survive a
#: restart, which is the whole difference between this and the throwaway cluster
#: the test suite provisions.
MANAGED_DIR = REPO / ".data" / "postgres"

#: Not 5432. A machine with PostgreSQL already installed has a service on the
#: default port, and binding next to it rather than fighting it is what lets
#: both exist.
MANAGED_PORT = 55432

#: Trust auth, so there is no password to know, lose or store. Safe because the
#: cluster listens on loopback only and holds nothing but local development
#: data - the same trade the test harness already makes.
MANAGED_URL = f"postgresql+asyncpg://postgres@127.0.0.1:{MANAGED_PORT}/aether"

WINDOWS_POSTGRES_ROOTS = (
    Path(r"C:\Program Files\PostgreSQL"),
    Path(r"C:\Program Files (x86)\PostgreSQL"),
)


def postgres_binary(name: str) -> Path | None:
    """Locate a Postgres binary on PATH, or under a standard install root."""
    if (found := shutil.which(name)) is not None:
        return Path(found)
    for root in WINDOWS_POSTGRES_ROOTS:
        if not root.is_dir():
            continue
        for version_dir in sorted(root.iterdir(), reverse=True):  # 17 before 16
            candidate = version_dir / "bin" / f"{name}.exe"
            if candidate.exists():
                return candidate
    return None


def managed_running() -> bool:
    return listening("127.0.0.1", MANAGED_PORT, timeout=1.0)


def start_managed() -> str | None:
    """Create the cluster if it does not exist, start it, ensure the database.

    Returns the URL, or None when the tools to do it are not installed.

    This exists because the alternative first-run experience is a superuser
    password the developer has to know: `createuser` and `createdb` against an
    existing service both need one, and on a machine where PostgreSQL was
    installed once and its password forgotten, that is a wall rather than a
    step. A cluster we create ourselves has no such wall.
    """
    initdb = postgres_binary("initdb")
    pg_ctl = postgres_binary("pg_ctl")
    if initdb is None or pg_ctl is None:
        return None

    datadir = MANAGED_DIR / "data"
    logfile = MANAGED_DIR / "postgres.log"

    if not (datadir / "PG_VERSION").exists():
        say(f"creating     {colour(str(datadir), DIM)}")
        datadir.parent.mkdir(parents=True, exist_ok=True)
        created = subprocess.run(
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
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if created.returncode != 0:
            print((created.stderr or created.stdout)[-800:], file=sys.stderr)
            fail("could not create a local database cluster.")

    if not managed_running():
        say(f"starting     {colour(f'postgres on {MANAGED_PORT}', DIM)}")
        started = subprocess.run(
            [
                str(pg_ctl),
                "-D",
                str(datadir),
                "-l",
                str(logfile),
                "-w",
                "-t",
                "60",
                "-o",
                f"-p {MANAGED_PORT} -c listen_addresses=127.0.0.1",
                "start",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if started.returncode != 0:
            print((started.stderr or started.stdout)[-800:], file=sys.stderr)
            fail(
                "the local cluster would not start.",
                fix=f"look in {logfile}",
            )

    # `createdb` against our own trust-auth cluster needs no password.
    createdb = postgres_binary("createdb")
    if createdb is not None:
        subprocess.run(
            [
                str(createdb),
                "-U",
                "postgres",
                "-h",
                "127.0.0.1",
                "-p",
                str(MANAGED_PORT),
                "aether",
            ],
            capture_output=True,
            text=True,
            check=False,  # "already exists" is the normal case after the first run.
        )
    return MANAGED_URL


def stop_managed() -> None:
    pg_ctl = postgres_binary("pg_ctl")
    if pg_ctl is None or not managed_running():
        return
    subprocess.run(
        [
            str(pg_ctl),
            "-D",
            str(MANAGED_DIR / "data"),
            "-m",
            "fast",
            "-w",
            "-t",
            "30",
            "stop",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


# --- preflight ---------------------------------------------------------------


def reachable(url: str) -> bool:
    """Whether something is listening where this URL points."""
    parts = urlsplit(url.replace("+asyncpg", ""))
    return listening(parts.hostname or "localhost", parts.port or 5432, timeout=2.0)


def usable(python: Path, url: str) -> bool:
    """Whether we can actually *connect* - the half a port check cannot answer.

    A machine with PostgreSQL installed answers on 5432 and then refuses the
    credentials, because the role this application expects was never created.
    Treating "the port is open" as "the database is ready" is what turns that
    into a migration failure three steps later.
    """
    return detect(python, url, CONNECT_PROBE)


CONNECT_PROBE = """
import asyncio, os, asyncpg
async def main():
    try:
        conn = await asyncpg.connect(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
    except Exception:
        print("no")
        return
    await conn.close()
    print("yes")
asyncio.run(main())
"""


def resolve_database(
    python: Path, url: str, *, configured: bool, own: bool
) -> tuple[str, bool]:
    """Pick the database to use, and say whether we are managing it.

    Order: an explicitly configured URL wins and is never second-guessed - if
    someone set DATABASE_URL, a launcher quietly using a different database is
    the worst thing it could do. Otherwise the default is tried, and a managed
    cluster is the fallback.
    """
    if own:
        managed = start_managed()
        if managed is None:
            fail(
                "--own-db needs the PostgreSQL client tools (initdb, pg_ctl).",
                fix="install PostgreSQL, or point DATABASE_URL at a database you have",
            )
        say(f"database     {colour(f'managed, 127.0.0.1:{MANAGED_PORT}', DIM)}")
        return managed, True

    if configured:
        if not reachable(url):
            parts = urlsplit(url.replace("+asyncpg", ""))
            fail(
                f"DATABASE_URL points at {parts.hostname}:{parts.port or 5432}, "
                "which is not answering.",
                fix="start it, or unset DATABASE_URL to use a managed local one",
            )
        say(f"database     {colour('DATABASE_URL', DIM)}")
        return url, False

    if reachable(url) and usable(python, url):
        parts = urlsplit(url.replace("+asyncpg", ""))
        say(f"database     {colour(f'{parts.hostname}:{parts.port or 5432}', DIM)}")
        return url, False

    # Either nothing is there, or something is there that will not let us in.
    managed = start_managed()
    if managed is None:
        fail(
            "no usable database, and the PostgreSQL client tools are not installed.",
            fix="install PostgreSQL, or set DATABASE_URL to a database you can reach",
        )
    say(f"database     {colour(f'managed, 127.0.0.1:{MANAGED_PORT}', DIM)}")
    return managed, True


def python_bin() -> Path:
    """The API's interpreter, or a clear failure.

    Called directly rather than through `uv run`, which costs about three
    seconds per invocation and would pay it on every process started here.
    """
    candidate = API / ".venv" / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    if not candidate.exists():
        fail(
            "the API virtualenv is missing.",
            fix="make api-install",
        )
    return candidate


def check_web_dependencies() -> None:
    if not (REPO / "node_modules").exists():
        fail(
            "node_modules is missing.",
            fix="npm ci   (or `npm install --legacy-peer-deps` for a cold resolve)",
        )


def detect(python: Path, url: str, code: str) -> bool:
    """Run a probe in the API environment; True when it prints `yes`."""
    try:
        done = subprocess.run(
            [str(python), "-c", code],
            cwd=API,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env={**os.environ, "DATABASE_URL": url},
        )
    except subprocess.TimeoutExpired:
        return False
    return done.stdout.strip().endswith("yes")


PGVECTOR_PROBE = """
import asyncio, os, asyncpg
from urllib.parse import urlsplit
async def main():
    dsn = os.environ["DATABASE_URL"].replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchval(
            "select 1 from pg_available_extensions where name = 'vector'"
        )
    finally:
        await conn.close()
    print("yes" if row else "no")
asyncio.run(main())
"""


def check_redis(env: dict[str, str]) -> bool:
    url = (
        os.environ.get("REDIS_URL")
        or env.get("REDIS_URL")
        or "redis://localhost:6379/0"
    )
    parts = urlsplit(url)
    return listening(parts.hostname or "localhost", parts.port or 6379, timeout=0.5)


# --- migration ---------------------------------------------------------------


def migrate(python: Path, url: str, *, target: str) -> None:
    say(f"migrating    {colour(target, DIM)}")
    done = subprocess.run(
        [str(python), "-m", "alembic", "upgrade", target],
        cwd=API,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        output = done.stderr or done.stdout
        diagnosis(output, url)
        tail = output.strip().splitlines()[-12:]
        print("\n".join(tail), file=sys.stderr)
        fail("the migration failed.")


def diagnosis(output: str, url: str) -> None:
    """Turn the three common first-run failures into the command that fixes them.

    A Postgres that is running but has never heard of this application is the
    normal state of a fresh clone, and asyncpg reports it as an authentication
    error - which reads like a wrong password rather than a missing role, and
    sends people to edit `.env` instead of creating a user.
    """
    parts = urlsplit(url.replace("+asyncpg", ""))
    user, database = parts.username or "aether", (parts.path or "/aether").lstrip("/")
    password = parts.password or "aether"

    missing_role = "InvalidPasswordError" in output or "does not exist" in output
    missing_db = "InvalidCatalogNameError" in output

    if not (missing_role or missing_db):
        return

    print(
        f"\n  {colour('This database has not been set up for Aether yet.', BOLD)}\n"
        f"  As a Postgres superuser, once:\n\n"
        f"    createuser  --createdb --pwprompt {user}\n"
        f"    createdb    --owner {user} {database}\n\n"
        f"  or the same thing in psql:\n\n"
        f"    CREATE ROLE {user} LOGIN PASSWORD '{password}';\n"
        f"    CREATE DATABASE {database} OWNER {user};\n",
        file=sys.stderr,
    )


# --- processes ---------------------------------------------------------------


class Process:
    def __init__(
        self, name: str, argv: list[str], *, cwd: Path, env: dict[str, str]
    ) -> None:
        self.name = name
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.popen: subprocess.Popen[str] | None = None

    def start(self) -> None:
        kwargs: dict[str, object] = {}
        if IS_WINDOWS:
            # Its own process group, so Ctrl-C in this console does not race us
            # to the children; we stop them deliberately below.
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        self.popen = subprocess.Popen(
            self.argv,
            cwd=self.cwd,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            **kwargs,  # type: ignore[arg-type]
        )
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.popen is not None and self.popen.stdout is not None
        tag = colour(f"{self.name:>6} |", COLOURS.get(self.name, ""))
        for line in self.popen.stdout:
            print(f"{tag} {line.rstrip()}", flush=True)

    def alive(self) -> bool:
        return self.popen is not None and self.popen.poll() is None

    def stop(self) -> None:
        if self.popen is None or self.popen.poll() is not None:
            return
        if IS_WINDOWS:
            # npm and uvicorn --reload both spawn children that outlive a plain
            # terminate, and an orphaned uvicorn keeps port 8000 so the next
            # `make start` fails with something unrelated to the cause.
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(self.popen.pid)],
                capture_output=True,
                check=False,
            )
        else:
            try:
                os.killpg(os.getpgid(self.popen.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                self.popen.terminate()
        try:
            self.popen.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.popen.kill()


def wait_for(
    name: str, port: int, *, deadline: float, processes: list[Process]
) -> bool:
    """Poll a port until it answers, or until something died trying."""
    until = time.monotonic() + deadline
    while time.monotonic() < until:
        if port_owner(port):
            return True
        for process in processes:
            if not process.alive():
                return False
        time.sleep(0.4)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-web", action="store_true", help="API and worker only")
    parser.add_argument(
        "--no-worker", action="store_true", help="do not start the worker"
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="serve the frontend from fixtures, not the API",
    )
    parser.add_argument(
        "--own-db",
        action="store_true",
        help="always use the managed cluster under .data/postgres",
    )
    args = parser.parse_args()

    print()
    say(colour("Aether Research", BOLD))

    env_file = read_dotenv()
    python = python_bin()

    configured = bool(os.environ.get("DATABASE_URL") or env_file.get("DATABASE_URL"))
    url, managing = resolve_database(
        python, database_url(env_file), configured=configured, own=args.own_db
    )
    if not args.no_web:
        check_web_dependencies()
        if shutil.which("npm") is None:
            fail("npm is not on PATH.")

    has_redis = check_redis(env_file)
    has_pgvector = detect(python, url, PGVECTOR_PROBE)

    base = {**os.environ, "DATABASE_URL": url}

    if has_redis:
        say(f"Redis        {colour('found', DIM)}")
    else:
        warn(
            "Redis is not running. Using the in-memory queue, so the API and the "
            "worker cannot share it -"
        )
        print(
            "  the worker's reconciliation sweep picks queued runs out of Postgres "
            "instead, a few seconds later.",
            flush=True,
        )
        base["APP_ENV"] = "test"
        # A deployment waits a minute before treating a queued run as lost. That
        # is the right default there and an eternity in front of a developer.
        base.setdefault("WORKER_QUEUED_GRACE_SECONDS", "5")
        base.setdefault("WORKER_POLL_SECONDS", "2")

    if has_pgvector:
        say(f"pgvector     {colour('found', DIM)}")
        target = "heads"
    else:
        warn("pgvector is not available. Retrieval will run its lexical arm only.")
        target = "core@head"

    migrate(python, url, target=target)

    for port, what in ((API_PORT, "the API"), (WEB_PORT, "the web app")):
        if (port == WEB_PORT and args.no_web) or port_owner(port) is False:
            continue
        fail(
            f"port {port} is already in use, which is where {what} goes.",
            fix="stop whatever is holding it, or close a previous `make start`",
        )

    processes: list[Process] = [
        Process(
            "api",
            [
                str(python),
                "-m",
                "uvicorn",
                "app.main:app",
                "--reload",
                "--port",
                str(API_PORT),
            ],
            cwd=API,
            env=base,
        )
    ]
    if not args.no_worker:
        processes.append(
            Process(
                "worker", [str(python), "-m", "app.workers.runner"], cwd=API, env=base
            )
        )
    if not args.no_web:
        processes.append(
            Process(
                "web",
                [
                    "npm.cmd" if IS_WINDOWS else "npm",
                    "run",
                    "dev",
                    "--workspace",
                    "@aether/web",
                ],
                cwd=REPO,
                env={
                    **base,
                    # The one setting that decides whether the frontend talks to
                    # this API or to its own fixtures (ADR 0009).
                    "NEXT_PUBLIC_API_MODE": "mock" if args.mock else "live",
                    "NEXT_PUBLIC_API_BASE_URL": f"http://localhost:{API_PORT}/api/v1",
                },
            )
        )

    print()
    for process in processes:
        process.start()

    try:
        ready = wait_for("api", API_PORT, deadline=90, processes=processes)
        if not ready:
            warn("the API did not come up; its output is above.")
        elif not args.no_web:
            # Next's first compile is slow enough to look like a hang.
            if wait_for("web", WEB_PORT, deadline=180, processes=processes):
                print()
                say(colour(f"ready  ->  http://localhost:{WEB_PORT}", BOLD))
                mode = "fixtures" if args.mock else "the live API"
                print(
                    f"  {colour('the frontend is serving ' + mode + '.', DIM)}\n"
                    f"  {colour(f'API docs: http://localhost:{API_PORT}/docs', DIM)}\n"
                    f"  {colour('Ctrl-C stops everything.', DIM)}\n",
                    flush=True,
                )
            else:
                warn("the web app did not come up; its output is above.")
        else:
            print()
            say(colour(f"ready  ->  http://localhost:{API_PORT}/docs", BOLD))

        while True:
            for process in processes:
                if not process.alive():
                    code = process.popen.returncode if process.popen else "?"
                    warn(f"{process.name} exited ({code}). Stopping the rest.")
                    return 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        print()
        say("stopping")
        return 0
    finally:
        for process in reversed(processes):
            process.stop()
        if managing:
            # Stopped, not left running: an orphaned postmaster is the thing
            # this repository has been bitten by before. The data stays in
            # .data/postgres, so the next start is instant and nothing is lost.
            stop_managed()


if __name__ == "__main__":
    raise SystemExit(main())
