"""Measure what the research pipeline does under load (Phase 21).

    uv run python scripts/with_test_db.py uv run python scripts/loadtest.py

That form provisions a throwaway Postgres, migrates it, and runs each declared
profile against a real worker: the real queue, the real lease, the real graph,
all nine real agents, the real toolbelt behind its SSRF guard, real ingestion,
real retrieval, the real projections and real report assembly. Point
``DATABASE_URL`` at an existing database to skip the provisioning.

**Two things are scripted, and they are the same two the scenario suite
scripts** (``tests/scenarios/world.py``): the model, by a provider that answers
from a script at a declared latency, and the socket, by a mock transport behind
the real guarded client. Those are where money and the open internet are. A
load test that made real model calls would measure a vendor's queue rather than
this system's, would cost hundreds of dollars per profile, and would not be
reproducible - so the provider's latency becomes an *input*, printed beside
every number it produced.

What that buys is the question a load test should answer: with the provider
held constant, how much research can this system get through, how deep does
the backlog get, how long does a run wait, and which resource runs out first.
What it cannot answer is how a real provider behaves at a hundred concurrent
calls. The report says so.

Run one profile with ``--profile offered-10``, or vary the load directly with
``--offered 8 --concurrency 2``. ``--out`` writes the JSON and the Markdown.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime as dt
import json
import platform
import shutil
import sys
import tempfile
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import select
from tests.scenarios import story, world
from tests.support import worker as harness
from tests.support.postgres import apply_migrations

from app.core.config import Settings
from app.core.enums import RunStatus
from app.db.base import Base
from app.db.models.research import ResearchRunRow
from app.db.models.trace import AgentRunRow
from app.db.session import Database
from app.loadtest import (
    DEFAULT_PROFILES,
    Breakdown,
    LoadProfile,
    LoadResult,
    LoadSuite,
    Sample,
    Sampler,
    breakdown_of,
)
from app.loadtest.profiles import profile_named
from app.loadtest.report import render_markdown
from app.loadtest.results import RunOutcome
from app.storage import build_object_storage
from app.workers.queue import InMemoryJobQueue

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO_ROOT / "data" / "loadtest"


def settings_for(profile: LoadProfile, database_url: str, storage_root: Path) -> Settings:
    """Production settings, with the profile's capacity and the test backends.

    Every value the profile sets is a setting a deployment has. The rest come
    from ``scenario_settings``, which is the scenario suite's own turn-down of
    the worker's timings - shared deliberately, so that the system this
    measures is the system those fifteen tests prove correct.
    """
    base = Settings(
        app_env="test",
        log_level="warning",
        database_url=database_url,
        storage_local_path=storage_root,
        openai_api_key=None,
        anthropic_api_key=None,
        # The load is what should saturate the pool, not an artificially small
        # pool. Ten and five are the shipped defaults.
        db_pool_size=10,
        db_max_overflow=5,
        # A per-user ceiling would refuse most of the load before it reached
        # the queue, and this is measuring the queue. Every run here belongs to
        # its own user in any case; see `offer`.
        max_concurrent_runs_per_user=1000,
    )
    return world.scenario_settings(
        base,
        worker_concurrency=profile.worker_concurrency,
        # The sweep is a repair mechanism and its round trip is real load.
        # Left at the scenario value rather than turned off: a load test that
        # silenced the background work would measure a worker no deployment
        # runs.
        worker_poll_seconds=0.05,
        worker_sweep_interval_seconds=1.0,
    )


async def offer(
    database: Database,
    settings: Settings,
    queue: InMemoryJobQueue,
    profile: LoadProfile,
) -> list[uuid.UUID]:
    """Create the profile's runs and put them on the queue.

    One user per run. Sharing one would make every row contend on the same
    per-user aggregates, which is a real effect but not this profile's - and
    a hundred runs from one account is not the load a deployment sees.
    """
    ids: list[uuid.UUID] = []
    gap = profile.arrival_seconds / profile.offered_jobs if profile.arrival_seconds else 0.0
    for index in range(profile.offered_jobs):
        run = await harness.seed_run(
            database,
            settings,
            mode=profile.mode,
            question=f"{story.QUESTION} (load {profile.name} #{index + 1})",
        )
        await queue.enqueue(run.id)
        ids.append(run.id)
        if gap:
            await asyncio.sleep(gap)
    return ids


async def outcomes_of(database: Database, ids: Sequence[uuid.UUID]) -> list[RunOutcome]:
    """Read every run's timestamps back from its row."""
    async with database.session() as session:
        rows = (
            (await session.execute(select(ResearchRunRow).where(ResearchRunRow.id.in_(list(ids)))))
            .scalars()
            .all()
        )
    return [
        RunOutcome(
            run_id=row.id,
            status=RunStatus(row.status),
            created_at=row.created_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
        )
        for row in rows
    ]


async def settled(database: Database, ids: Sequence[uuid.UUID]) -> int:
    """How many of these runs a worker has finished with."""
    async with database.session() as session:
        rows = (
            (
                await session.execute(
                    select(ResearchRunRow.status).where(ResearchRunRow.id.in_(list(ids)))
                )
            )
            .scalars()
            .all()
        )
    return sum(1 for status in rows if RunStatus(status).is_terminal)


def warm_up() -> None:
    """Pay the process's cold imports before the first profile is timed.

    LlamaIndex costs about four and a half seconds to import and the chunker
    imports it lazily, so whichever profile runs first is charged for it. That
    is a real cost a worker pays once at start-up and never again - but
    charging it to ``offered-10`` and not to the other three would make the
    smallest load look like the slowest system, which is a measurement artefact
    and not a finding.
    """
    from llama_index.core.node_parser import SentenceSplitter  # noqa: F401


async def reset(database: Database) -> None:
    """Empty every table between profiles.

    The corpus a profile ingests is part of that profile's load and grows
    inside it, which is realistic. Carrying it into the *next* profile is not:
    the full-text arm would then search a table that the previous profile
    filled, and the four rows of the report would stop being comparable -
    which is the only reason to run four of them.
    """
    statement = (
        "TRUNCATE "
        + ", ".join(f'"{name}"' for name in Base.metadata.tables)
        + " RESTART IDENTITY CASCADE"
    )
    async with database.session() as session:
        await session.execute(sa.text(statement))


async def transactions(database: Database) -> int | None:
    """Committed transactions on this database so far, from Postgres itself.

    Two readings and a subtraction give the profile's exact round-trip count -
    a number cProfile cannot supply, because it counts every resumption of a
    coroutine as a call and an async context manager therefore reports several
    times the sessions that were actually opened. Inferring round trips from a
    CPU profile of asyncio code is how a plausible wrong number gets published.

    ``None`` when the counter cannot be read, which is *not measured*.
    """
    try:
        async with database.session() as session:
            return int(
                (
                    await session.execute(
                        sa.text(
                            "SELECT xact_commit FROM pg_stat_database "
                            "WHERE datname = current_database()"
                        )
                    )
                ).scalar_one()
            )
    except Exception:
        return None


async def breakdown_for(database: Database, ids: Sequence[uuid.UUID]) -> Breakdown | None:
    """Where these runs' time went, from the ledger they wrote (Phase 22).

    No instrument is added for this: Phase 16 already opens an ``agent_runs``
    row per node execution with its own latency, so profiling starts as a
    query over what the system recorded while doing its job.
    """
    async with database.session() as session:
        rows = (
            await session.execute(
                select(AgentRunRow.agent_name, AgentRunRow.latency_ms).where(
                    AgentRunRow.run_id.in_(list(ids))
                )
            )
        ).all()
    if not rows:
        return None
    return breakdown_of([(row[0], row[1]) for row in rows], runs=len(ids))


async def has_pgvector(database: Database) -> bool:
    """Whether the extension is installed. Answerable before the schema exists."""
    async with database.session() as session:
        return bool(
            (
                await session.execute(
                    sa.text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
                )
            ).scalar_one()
        )


async def run_profile(
    profile: LoadProfile,
    *,
    database: Database,
    storage_root: Path,
    database_url: str,
    verbose: bool = True,
) -> LoadResult:
    """One profile, from an empty queue to every run settled."""
    settings = settings_for(profile, database_url, storage_root)
    storage = build_object_storage(settings)
    brain = story.ordinary_run()
    brain.latency_seconds = profile.model_latency_seconds
    scripted = world.Web(pages=[story.PRICE_LIST, story.MARKET_REVIEW])

    built = world.build_world(
        database=database,
        storage=storage,
        settings=settings,
        web=scripted,
        brain=brain,
        worker_id=f"load-{profile.name}",
    )
    worker = built.harness.worker
    queue = built.harness.queue
    gateway = built.dependencies.gateway
    pool = database.engine.pool

    async def probe(at: float) -> Sample:
        return Sample(
            at=at,
            queue_depth=await queue.depth(),
            running=worker.active,
            db_in_use=pool.checkedout(),
            db_idle=pool.checkedin(),
            db_overflow=max(0, pool.overflow()),
            llm_in_flight=gateway.saturation.in_flight,
        )

    ids = await offer(database, settings, queue, profile)
    # After the rows are seeded, so the count is the work the *runs* did.
    commits_before = await transactions(database)
    started_at = dt.datetime.now(dt.UTC)
    loop = asyncio.get_running_loop()
    began = loop.time()

    with world.scripted_dns():
        async with Sampler(
            probe=probe, interval_seconds=profile.sample_interval_seconds
        ) as sampler:
            served = asyncio.create_task(worker.serve(), name=f"load-worker-{profile.name}")
            try:
                async with asyncio.timeout(profile.deadline_seconds):
                    while await settled(database, ids) < len(ids):
                        if served.done():
                            await served
                            raise RuntimeError("the worker stopped before the load finished")
                        await asyncio.sleep(0.2)
            except TimeoutError:
                # Not an error: a profile that does not drain inside its
                # deadline is a measurement, and the outcomes below record
                # exactly how far it got.
                if verbose:
                    print(
                        f"  [{profile.name}] deadline reached with "
                        f"{await settled(database, ids)}/{len(ids)} settled",
                        file=sys.stderr,
                    )
            finally:
                worker.stop()
                await asyncio.wait_for(served, timeout=profile.deadline_seconds)

    wall_seconds = loop.time() - began
    saturation = gateway.saturation
    commits_after = await transactions(database)
    await built.close()

    return LoadResult(
        profile=profile,
        started_at=started_at,
        wall_seconds=wall_seconds,
        outcomes=tuple(await outcomes_of(database, ids)),
        timeline=sampler.timeline(capacity=profile.capacity),
        saturation=saturation,
        breakdown=await breakdown_for(database, ids),
        commits=(
            None
            if commits_before is None or commits_after is None
            else commits_after - commits_before
        ),
        not_measured={
            "redis_utilisation": (
                "No Redis on this machine. `APP_ENV=test` selects the in-memory "
                "queue adapter, so queue depth is measured and Redis is not."
            ),
            "provider_behaviour": (
                "The model is scripted at a declared latency, so nothing here "
                "measures a real provider's throttling or tail latency."
            ),
            "embedding_and_dense_retrieval": (
                "No pgvector locally: ingestion stores chunks with vectors "
                "pending and retrieval runs its lexical arm only."
            ),
        },
    )


def conditions(profiles: Sequence[LoadProfile]) -> dict[str, str]:
    """What has to travel with the numbers for them to mean anything."""
    latencies = sorted({profile.model_latency_seconds for profile in profiles})
    return {
        "measured": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "machine": f"{platform.processor() or platform.machine()}, {platform.system()}",
        "python": platform.python_version(),
        "what is real": (
            "Postgres, the job queue, the lease, the LangGraph graph, all nine "
            "agents, the toolbelt with its SSRF guard, ingestion, retrieval, the "
            "evidence projection and report assembly."
        ),
        "what is scripted": (
            "The model provider (behind the real gateway, answering at "
            f"{', '.join(f'{value:g}s' for value in latencies)} per call) and the "
            "socket (behind the real guarded client)."
        ),
        "queue": "In-memory adapter (`APP_ENV=test`). Redis is not installed here.",
        "retrieval": "Lexical arm only - no pgvector on this machine.",
        "caveat": (
            "These are this machine's numbers, and its CPU runs at about a third "
            "of nominal. Read them as ratios between profiles, not as a capacity "
            "figure for a deployment."
        ),
    }


async def main_async(args: argparse.Namespace) -> int:
    database_url = args.database_url
    if not database_url:
        print(
            "No DATABASE_URL. Run under scripts/with_test_db.py, or set it.",
            file=sys.stderr,
        )
        return 2

    profiles = _selected(args)
    out = Path(args.out)
    # The object store is a by-product of the load, not an artefact of it:
    # a hundred runs write a hundred documents, and none of them belongs in
    # the repository next to the results.
    workspace = tempfile.mkdtemp(prefix="aether-loadtest-")
    storage_root = Path(workspace) / "object-storage"

    database = Database(
        Settings(
            app_env="test",
            log_level="warning",
            database_url=database_url,
            storage_local_path=storage_root,
        )
    )
    results: list[LoadResult] = []
    try:
        if not args.no_migrate:
            # In a worker thread: Alembic's env.py calls asyncio.run itself,
            # and it cannot do that from inside this one's running loop.
            await asyncio.to_thread(
                apply_migrations, database_url, with_vector=await has_pgvector(database)
            )
        warm_up()
        for profile in profiles:
            await reset(database)
            print(
                f"[loadtest] {profile.name}: {profile.offered_jobs} jobs, "
                f"{profile.capacity} slots, {profile.model_latency_seconds:g}s per model call",
                file=sys.stderr,
            )
            result = await run_profile(
                profile,
                database=database,
                storage_root=storage_root,
                database_url=database_url,
            )
            results.append(result)
            print(
                f"  -> {result.completed}/{result.offered} completed in "
                f"{result.wall_seconds:.1f}s "
                f"({result.throughput_per_minute:.2f} runs/min)",
                file=sys.stderr,
            )
    finally:
        await database.dispose()
        shutil.rmtree(workspace, ignore_errors=True)

    suite = LoadSuite(results=tuple(results), conditions=conditions(profiles))
    (out / "results.json").write_text(
        json.dumps(suite.as_dict(), indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    markdown = render_markdown(suite)
    (out / "results.md").write_text(markdown, encoding="utf-8", newline="\n")
    print(markdown)
    return 0


def _selected(args: argparse.Namespace) -> tuple[LoadProfile, ...]:
    """The profiles this invocation asked for."""
    if args.offered:
        return (
            LoadProfile(
                name=f"offered-{args.offered}",
                offered_jobs=args.offered,
                worker_concurrency=args.concurrency,
                model_latency_seconds=args.model_latency or 0.0,
                deadline_seconds=args.deadline,
            ),
        )
    chosen = DEFAULT_PROFILES if not args.profile else tuple(profile_named(n) for n in args.profile)
    if args.model_latency is not None:
        import dataclasses

        chosen = tuple(
            dataclasses.replace(profile, model_latency_seconds=args.model_latency)
            for profile in chosen
        )
    return chosen


def parse(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", action="append", help="A declared profile name; repeatable.")
    parser.add_argument("--offered", type=int, help="Ad-hoc load: this many jobs.")
    parser.add_argument("--concurrency", type=int, default=4, help="Worker execution slots.")
    parser.add_argument(
        "--model-latency",
        type=float,
        default=None,
        help="Seconds the scripted provider takes per call. Declared, not measured.",
    )
    parser.add_argument("--deadline", type=float, default=900.0, help="Per-profile ceiling.")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Where the artefacts are written.")
    parser.add_argument("--database-url", default=None)
    parser.add_argument(
        "--cprofile",
        default=None,
        help="Write a cProfile of the whole run here (Phase 22).",
    )
    parser.add_argument(
        "--no-migrate",
        action="store_true",
        help="Assume the schema is already there. The default migrates it.",
    )
    return parser.parse_args(list(argv))


@contextlib.contextmanager
def profiled(path: Path) -> Iterator[None]:
    """A cProfile around the whole run, written to ``path``.

    The ledger breakdown says *which node* is expensive; this says which
    functions inside it are. Deliberately the second instrument rather than the
    first: a CPU profile of an asyncio worker is thousands of frames of
    scheduler, and it only becomes readable once you already know what you are
    looking for.
    """
    import cProfile

    profiler = cProfile.Profile()
    profiler.enable()
    try:
        yield
    finally:
        profiler.disable()
        profiler.dump_stats(str(path))
        print(f"[loadtest] cProfile written to {path}", file=sys.stderr)


def main(argv: Sequence[str]) -> int:
    import os

    args = parse(argv)
    if args.database_url is None:
        args.database_url = os.environ.get("DATABASE_URL")
    Path(args.out).mkdir(parents=True, exist_ok=True)
    # psycopg's async mode refuses Windows' proactor loop, and the graph's
    # checkpointer runs on psycopg. The worker has the same requirement.
    with profiled(Path(args.cprofile)) if args.cprofile else contextlib.nullcontext():
        return asyncio.run(main_async(args), loop_factory=_loop_factory())


def _loop_factory() -> object:
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop
    return asyncio.new_event_loop


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
