"""LangGraph's checkpointer against real Postgres: schema, resume, cancellation.

The in-memory saver in the graph tests uses the same serializer, but three
things only exist here: the tables migration 0005 builds, psycopg talking to a
real server, and a cancellation written the way the API writes one. On Windows
these tests run on a selector event loop, which psycopg's async mode requires
(see this directory's conftest).
"""

from __future__ import annotations

import importlib.util
import re
import uuid
from pathlib import Path
from types import ModuleType

import asyncpg
import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import update

from app.agents.checkpoint import open_checkpointer
from app.agents.errors import CheckpointSchemaOutdated, SynthesisFailed
from app.agents.schemas import ClaimItem, Plan, RunClock, StopReason
from app.core.enums import ResearchMode, RunStatus
from app.db.external import CHECKPOINT_TABLES
from app.db.models.research import ResearchRunRow
from app.research.cancellation import PostgresCancellationProbe
from tests.support.graph import Script, ScriptedNodes, brief, make_runner
from tests.support.ingestion import seed_run as seed_bare_run
from tests.support.worker import (
    build_worker,
    entered,
    read_row,
    seed_run,
    serve_until,
    settled,
    worker_settings,
)

MIGRATION = Path(__file__).resolve().parents[2] / "migrations/versions/0005_graph_checkpoints.py"
LATEST_VERSION = len(AsyncPostgresSaver.MIGRATIONS) - 1


def migration_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("graph_checkpoints_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def normalised(statement: str) -> str:
    return " ".join(statement.split())


def dsn(settings) -> str:
    return settings.database_url.replace("+asyncpg", "")


# --- the schema -----------------------------------------------------------------


def test_the_frozen_ddl_is_the_installed_checkpointers_ddl():
    """Migration 0005 copies the library's statements rather than importing them.
    When an upgrade changes them, this fails and asks for a new revision - the
    alternative is a schema that silently stops matching the code that uses it."""
    frozen = [normalised(statement) for statement in migration_module().CHECKPOINT_MIGRATIONS]
    installed = [normalised(statement) for statement in AsyncPostgresSaver.MIGRATIONS]
    assert frozen == installed, (
        "langgraph-checkpoint-postgres changed its schema: add a migration that applies "
        "the new statements and records their versions."
    )


def test_the_tables_excused_from_drift_checks_are_the_ones_the_checkpointer_creates():
    created = {
        match.group(1)
        for statement in AsyncPostgresSaver.MIGRATIONS
        for match in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+)", statement)
    }
    assert created == CHECKPOINT_TABLES


async def test_the_migration_leaves_setup_nothing_to_do(settings):
    """The version the library would record, so its own setup() finds the schema current."""
    connection = await asyncpg.connect(dsn(settings))
    try:
        version = await connection.fetchval("SELECT max(v) FROM checkpoint_migrations")
    finally:
        await connection.close()
    assert version == LATEST_VERSION


async def test_the_checkpointer_refuses_a_schema_older_than_it_expects(settings):
    """At open, not on a worker's first write of a run it has already started."""
    connection = await asyncpg.connect(dsn(settings))
    try:
        await connection.execute("DELETE FROM checkpoint_migrations WHERE v = $1", LATEST_VERSION)
        with pytest.raises(CheckpointSchemaOutdated):
            async with open_checkpointer(settings):
                pass
    finally:
        await connection.execute(
            "INSERT INTO checkpoint_migrations (v) VALUES ($1) ON CONFLICT DO NOTHING",
            LATEST_VERSION,
        )
        await connection.close()


# --- resuming -------------------------------------------------------------------


async def test_a_run_resumes_from_postgres_with_its_values_still_typed(settings):
    """A new pool and a new runner - a different process, as far as the graph can
    tell - pick the run up at the node that failed, and every value comes back
    as the type it was stored as rather than as a dict."""
    # A limit is reached before the crash, so a stop is saved and must come back
    # as a Stop whose reason is the enum member, not the string.
    nodes = ScriptedNodes(Script(sufficient_on_round=None, fail_on={"synthesizer": {1}}))
    run = brief(max_sources=2)

    async with open_checkpointer(settings) as checkpointer:
        runner, _, _ = make_runner(nodes, checkpointer=checkpointer)
        with pytest.raises(SynthesisFailed):
            await runner.run(run)

    async with open_checkpointer(settings) as checkpointer:
        runner, _, _ = make_runner(nodes, checkpointer=checkpointer)
        state = await runner.run(run)

    assert nodes.calls["researcher"] == 2
    assert nodes.calls["synthesizer"] == 2
    assert isinstance(state["research_plan"], Plan)
    assert all(isinstance(claim, ClaimItem) for claim in state["claims"])
    assert isinstance(state["parameters"].mode, ResearchMode)
    assert state["stop"] is not None and state["stop"].reason is StopReason.SOURCES
    assert isinstance(state["clock"], RunClock)
    assert state["citation_check"] is not None and state["citation_check"].passed


# --- cancellation ---------------------------------------------------------------


async def test_a_cancellation_written_to_the_run_stops_the_graph(settings, database):
    """Written the way ResearchService.cancel writes it: the status column."""
    user_id, run_id = await seed_bare_run(database)
    nodes = ScriptedNodes()

    async def cancel_the_run() -> None:
        async with database.session() as session:
            await session.execute(
                update(ResearchRunRow)
                .where(ResearchRunRow.id == run_id)
                .values(status=RunStatus.CANCELLED.value)
            )

    nodes.script.after["planner"] = cancel_the_run

    async with open_checkpointer(settings) as checkpointer:
        runner, _, _ = make_runner(
            nodes, checkpointer=checkpointer, probe=PostgresCancellationProbe(database)
        )
        state = await runner.run(brief(research_id=run_id, user_id=user_id))

    assert nodes.calls["researcher"] == 0
    assert nodes.calls["synthesizer"] == 0
    assert state["stop"] is not None and state["stop"].reason is StopReason.CANCELLED


async def test_a_run_that_is_gone_or_not_the_users_reads_as_cancelled(database):
    """Nobody is left to deliver its report to, so carrying on would only spend."""
    user_id, run_id = await seed_bare_run(database)
    probe = PostgresCancellationProbe(database)

    assert await probe.is_cancelled(run_id, user_id) is False
    assert await probe.is_cancelled(run_id, uuid.uuid4()) is True
    assert await probe.is_cancelled(uuid.uuid4(), user_id) is True


# --- the worker across a restart -------------------------------------------------


async def test_a_worker_restart_resumes_the_run_from_postgres(settings, database, artifact_store):
    """Phase 13's headline promise, against the store that has to keep it.

    Two worker processes, each with its own checkpointer pool, and a run handed
    from one to the other by a shutdown. The second must continue from the node
    the first had not finished - not re-plan, and not research anything twice -
    because the checkpoint is written before each node starts, not after the run
    ends.
    """
    config = worker_settings(settings)
    run = await seed_run(database, config)

    async with open_checkpointer(config) as checkpointer:
        first = build_worker(
            database,
            artifact_store,
            config,
            script=Script(hang={"critic"}),
            checkpointer=checkpointer,
            worker_id="worker-1",
        )
        await first.queue.enqueue(run.id)
        await serve_until(first, entered(first, "critic"))

    handed_back = await read_row(database, run.id)
    assert RunStatus(handed_back.status) is RunStatus.PAUSED
    assert handed_back.attempts == 0, "a stopped worker returns the attempt it took"
    assert first.nodes.calls["researcher"] > 0

    async with open_checkpointer(config) as checkpointer:
        second = build_worker(
            database,
            artifact_store,
            config,
            nodes=ScriptedNodes(Script()),
            checkpointer=checkpointer,
            queue=first.queue,
            worker_id="worker-2",
        )
        await second.queue.enqueue(run.id)
        await serve_until(second, settled(second, database, run.id, RunStatus.COMPLETED))

    row = await read_row(database, run.id)
    assert RunStatus(row.status) is RunStatus.COMPLETED
    assert row.attempts == 1, "and the second worker's is the first attempt spent"
    assert second.nodes.calls["planner"] == 0, "the plan came back from Postgres"
    assert second.nodes.calls["researcher"] == 0, "and no research was repeated"
