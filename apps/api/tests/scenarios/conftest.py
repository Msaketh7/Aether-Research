"""Fixtures for the scenario suite.

One fixture, and it is a factory rather than a built world: a scenario decides
its own web, its own script and often its own budget, and a fixture that built
those for it would have to be configured by every test that used it.

The worlds a test builds are closed at the end of it, whatever the test did,
because each holds an HTTP client and a connection pool - and a suite that leaks
one per test exhausts the pool long before it exhausts the scenarios.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Iterator

import pytest

from app.core.config import Settings
from app.db.session import Database
from app.storage import ObjectStorage
from tests.scenarios.world import ScriptedBrain, Web, World, build_world, scenario_settings

WorldFactory = Callable[..., World]


@pytest.fixture(autouse=True)
def application_logging_at_the_level_a_deployment_runs() -> Iterator[None]:
    """Turn the ``app`` loggers up to INFO for every scenario.

    The rest of the suite pins ``log_level`` to ``warning`` because a test that
    asserts on behaviour does not want the output. That is also how three
    ``extra={"created": ...}`` fields survived to Phase 19: ``makeRecord``
    refuses an extra that shares a name with a ``LogRecord`` attribute, so those
    calls raise - but only once the logger is enabled at their level, and no
    test had ever enabled one. A scenario suite claiming to run the system a
    deployment runs has to run its logging too.
    """
    logger = logging.getLogger("app")
    previous = logger.level
    logger.setLevel(logging.INFO)
    try:
        yield
    finally:
        logger.setLevel(previous)


@pytest.fixture
async def make_world(
    settings: Settings, database: Database, artifact_store: ObjectStorage
) -> AsyncIterator[WorldFactory]:
    """Build a world, and close every one built, however the test ended."""
    built: list[World] = []

    def factory(
        *, web: Web, brain: ScriptedBrain, worker_id: str = "worker-1", **overrides: object
    ) -> World:
        world = build_world(
            database=database,
            storage=artifact_store,
            settings=scenario_settings(settings, **overrides),
            web=web,
            brain=brain,
            worker_id=worker_id,
        )
        built.append(world)
        return world

    try:
        yield factory
    finally:
        for world in built:
            await world.close()
