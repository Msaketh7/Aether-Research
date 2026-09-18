"""``python -m app.evaluations``: run a benchmark and report it.

A third process type, and deliberately not a fourth long-lived one: this
starts, executes a dataset against the build it is run from, writes one row
per case, prints a report, and exits with 0 or 1 so CI can gate on it.

**It costs real money and it says so.** Each case is a real research run
through the real graph, which is the only way a benchmark measures the system
rather than a special path built for it. That is why it is a command someone
runs rather than something a test suite does, and why the shipped dataset has
four cases.

**It prints only what it measured.** A metric no case could measure is shown
as ``not measured``, never as a zero, and the exit code is decided by the
gates in ``app.evaluations.thresholds`` - which are all ungated until there is
a baseline to set them from.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from pathlib import Path

from app.agents.checkpoint import open_checkpointer
from app.agents.factory import build_dependencies, build_research_nodes
from app.agents.graph import GraphBounds
from app.agents.runtime import ResearchGraphRunner
from app.cache import build_cache
from app.core.config import Settings, get_settings
from app.core.enums import EvaluationKind, ResearchMode
from app.core.logging import configure_logging, get_logger
from app.db.repositories.evaluations import SqlAlchemyEvaluationStore
from app.db.session import Database
from app.evaluations.dataset import DATASET_DIR, Dataset, available, load_dataset
from app.evaluations.report import render
from app.evaluations.runner import BenchmarkRunner
from app.evaluations.thresholds import Thresholds
from app.models import build_gateway
from app.research.cancellation import PostgresCancellationProbe
from app.research.recorder import RunRecorder
from app.research.schemas import RunLimits
from app.storage import build_object_storage

logger = get_logger(__name__)


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluations",
        description="Run an evaluation dataset against this build.",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help=f"A dataset file. Defaults to every file in {DATASET_DIR}.",
    )
    parser.add_argument(
        "--user-id",
        type=uuid.UUID,
        default=None,
        help="The user the benchmark runs act as. A fresh id by default.",
    )
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in ResearchMode],
        default=None,
        help="Override every case's mode. For a cheap smoke run.",
    )
    return parser.parse_args(argv)


async def execute(settings: Settings, args: argparse.Namespace) -> int:
    """Run the datasets and return the process's exit code."""
    paths = [args.dataset] if args.dataset else available()
    if not paths:
        logger.error("no dataset found", extra={"directory": str(DATASET_DIR)})
        return 2

    database = Database(settings)
    cache = build_cache(settings)
    gateway = build_gateway(settings, cache=cache)
    dependencies = build_dependencies(
        settings, gateway=gateway, database=database, storage=build_object_storage(settings)
    )
    store = SqlAlchemyEvaluationStore(database)
    failed = False
    try:
        async with open_checkpointer(settings) as checkpointer:
            runner = BenchmarkRunner(
                graph=ResearchGraphRunner(
                    nodes=build_research_nodes(settings, dependencies=dependencies),
                    checkpointer=checkpointer,
                    probe=PostgresCancellationProbe(database),
                    bounds=GraphBounds.from_settings(settings),
                    recorder=RunRecorder(database),
                ),
                store=store,
                limits=RunLimits.for_mode(ResearchMode.DEEP, settings),
                thresholds=Thresholds(),
                model_config_used=_routing(settings),
            )
            for path in paths:
                dataset = _loaded(path, args.mode)
                result = await runner.run(
                    dataset,
                    user_id=args.user_id or uuid.uuid4(),
                    kind=EvaluationKind.FULL,
                )
                # This command's entire purpose is what it prints.
                print(render(result))
                failed = failed or not result.passed
    finally:
        await dependencies.close()
        await gateway.close()
        await cache.close()
        await database.dispose()
    return 1 if failed else 0


def _loaded(path: Path, mode: str | None) -> Dataset:
    dataset = load_dataset(path)
    if mode is None:
        return dataset
    override = ResearchMode(mode)
    cases = tuple(case.model_copy(update={"mode": override}) for case in dataset.cases)
    return dataset.model_copy(update={"cases": cases})


def _routing(settings: Settings) -> dict[str, str]:
    """The model configuration a result is attributed to.

    Recorded from the registry rather than described by hand: a report that
    named the models someone believed were in force would be worse than one
    that named none.
    """
    from app.models.registry import load_registry

    registry = load_registry(settings.model_registry_path)
    return {spec.key: spec.model_id for spec in registry}


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    args = parse(argv if argv is not None else sys.argv[1:])
    # A selector loop for the same reason the worker needs one: LangGraph's
    # Postgres checkpointer runs on psycopg, which refuses Windows' default.
    return asyncio.run(execute(settings, args), loop_factory=asyncio.SelectorEventLoop)


if __name__ == "__main__":
    sys.exit(main())
