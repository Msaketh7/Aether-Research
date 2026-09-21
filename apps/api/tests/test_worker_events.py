"""What a worker actually streams while it runs a research (Phase 14).

Phase 13 could say what a run's row looked like at the end. This is the other
half of the same question: what a person watching it was told while it
happened. Every test here drives the real worker over the real graph runner
against real Postgres, and reads the events back from the durable log - which
is where a reconnecting browser would read them from too.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.enums import ResearchEventType as E
from app.core.enums import RunStatus
from app.db.repositories.events import SqlAlchemyEventLog
from app.research.events import EventDraft, ResearchEvent
from app.research.repository import utcnow
from tests.support.graph import Script, ScriptedNodes
from tests.support.worker import (
    Harness,
    build_worker,
    drained,
    read_row,
    respawn,
    seed_run,
    serve_until,
    set_row,
    settled,
    status_is,
    worker_settings,
)

TERMINAL_OR_PAUSED = (
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.PAUSED,
)


@pytest.fixture
def worker_config(settings):
    return worker_settings(settings)


async def run_one(harness: Harness, database, run_id: uuid.UUID, *until: RunStatus) -> None:
    await harness.queue.enqueue(run_id)
    await serve_until(harness, settled(harness, database, run_id, *(until or TERMINAL_OR_PAUSED)))


async def streamed(database, run_id: uuid.UUID) -> list[ResearchEvent]:
    """Every event the run emitted, in the order a client would receive them."""
    return await SqlAlchemyEventLog(database).replay(run_id)


def types(events: list[ResearchEvent]) -> list[E]:
    return [event.type for event in events]


def first(events: list[ResearchEvent], kind: E) -> ResearchEvent:
    return next(event for event in events if event.type is kind)


# --- the shape of a whole run ---------------------------------------------


async def test_a_run_streams_the_vocabulary_the_frontend_renders(
    database, artifact_store, worker_config
):
    """The claim this phase exists to make: a worker narrates its run.

    Asserted as a set and an order rather than an exact list, because the
    number of sources and claims is the script's business and the sequence of
    *phases* is this phase's.
    """
    run = await seed_run(database, worker_config)
    harness = build_worker(
        database,
        artifact_store,
        worker_config,
        script=Script(sufficient_on_round=1, contradictions_on_round=1),
    )

    await run_one(harness, database, run.id)

    events = await streamed(database, run.id)
    emitted = types(events)
    assert set(emitted) >= {
        E.PLANNER_STARTED,
        E.PLANNER_COMPLETED,
        E.SUBTASK_STARTED,
        E.SEARCH_STARTED,
        E.SOURCE_FOUND,
        E.SOURCE_PROCESSED,
        E.SOURCES_PROGRESS,
        E.CLAIM_EXTRACTED,
        E.EVIDENCE_PROGRESS,
        E.VERIFICATION_STARTED,
        E.CONTRADICTION_FOUND,
        E.CRITIC_STARTED,
        E.ANSWER_STARTED,
        E.ANSWER_DELTA,
        E.ANSWER_COMPLETED,
        E.SYNTHESIS_STARTED,
        E.CITATION_CHECK,
        E.REPORT_COMPLETED,
    }
    # The phases arrive in the order they happened.
    assert emitted.index(E.PLANNER_COMPLETED) < emitted.index(E.SOURCE_FOUND)
    assert emitted.index(E.SOURCE_FOUND) < emitted.index(E.CLAIM_EXTRACTED)
    assert emitted.index(E.CLAIM_EXTRACTED) < emitted.index(E.VERIFICATION_STARTED)
    assert emitted.index(E.CRITIC_STARTED) < emitted.index(E.ANSWER_STARTED)
    # The answer before the report, which is the point of where the node sits.
    assert emitted.index(E.ANSWER_COMPLETED) < emitted.index(E.SYNTHESIS_STARTED)
    assert emitted.index(E.ANSWER_STARTED) < emitted.index(E.ANSWER_DELTA)
    assert emitted[-1] is E.REPORT_COMPLETED
    # Monotonic and gapless: this is the `id:` a browser echoes back.
    assert [event.seq for event in events] == list(range(1, len(events) + 1))


async def test_every_event_carries_the_status_the_run_was_in(
    database, artifact_store, worker_config
):
    """The header follows the stream without a second request (ADR 0006)."""
    run = await seed_run(database, worker_config)
    harness = build_worker(database, artifact_store, worker_config)

    await run_one(harness, database, run.id)

    events = await streamed(database, run.id)
    assert first(events, E.PLANNER_STARTED).status is RunStatus.PLANNING
    assert first(events, E.SOURCE_FOUND).status is RunStatus.RESEARCHING
    assert first(events, E.VERIFICATION_STARTED).status is RunStatus.VERIFYING
    assert first(events, E.ANSWER_STARTED).status is RunStatus.SYNTHESIZING
    assert first(events, E.SYNTHESIS_STARTED).status is RunStatus.SYNTHESIZING
    assert first(events, E.CITATION_CHECK).status is RunStatus.VALIDATING
    assert first(events, E.REPORT_COMPLETED).status is RunStatus.COMPLETED


async def test_a_source_is_described_the_way_the_reference_list_will_be(
    database, artifact_store, worker_config
):
    """A payload of real fields, not placeholders: the activity feed renders it."""
    run = await seed_run(database, worker_config)
    harness = build_worker(database, artifact_store, worker_config)

    await run_one(harness, database, run.id)

    found = first(await streamed(database, run.id), E.SOURCE_FOUND)
    assert found.payload["url"].startswith("https://example.com/")
    assert found.payload["publisher"] == "example.com"
    assert found.payload["source_type"] == "web"
    assert found.payload["task_external_id"].startswith("round1-task")
    # Nothing scores relevance yet, and the contract distinguishes that from a
    # low score. Zero here would be a measurement nobody made.
    assert found.payload["relevance_score"] is None

    processed = first(await streamed(database, run.id), E.SOURCE_PROCESSED)
    assert processed.payload["chunk_count"] >= 1


async def test_the_report_event_describes_the_report_a_reader_can_open(
    database, artifact_store, worker_config
):
    """Read back from the row, so the numbers are the ones on the page."""
    run = await seed_run(database, worker_config)
    harness = build_worker(database, artifact_store, worker_config)

    await run_one(harness, database, run.id)

    completed = first(await streamed(database, run.id), E.REPORT_COMPLETED)
    row = await read_row(database, run.id)
    assert completed.payload["word_count"] > 0
    assert completed.payload["cost_usd"] == pytest.approx(float(row.total_cost_usd), abs=1e-4)
    assert uuid.UUID(completed.payload["report_id"])


# --- more than one round --------------------------------------------------


async def test_a_second_round_is_announced_with_the_critic_s_own_reason(
    database, artifact_store, worker_config
):
    """`additional_research_requested` is emitted once the plan for it exists.

    Observed rather than predicted: the critic asking is not the same as the
    graph having another round, and only the second is worth telling a reader.
    """
    run = await seed_run(database, worker_config)
    harness = build_worker(
        database, artifact_store, worker_config, script=Script(sufficient_on_round=2)
    )

    await run_one(harness, database, run.id)

    events = await streamed(database, run.id)
    requested = first(events, E.ADDITIONAL_RESEARCH_REQUESTED)
    started = first(events, E.ITERATION_STARTED)
    assert requested.payload["iteration"] == 2
    assert requested.payload["new_task_count"] == 2
    assert started.payload["max_iterations"] == run.limits.max_iterations
    # Two rounds, two plans, and no plan announced twice.
    assert [
        event.payload["iteration"] for event in events if event.type is E.PLANNER_COMPLETED
    ] == [1, 2]


async def test_nothing_is_announced_twice_in_a_run_that_loops(
    database, artifact_store, worker_config
):
    """Every superstep sees the whole accumulated state, so the cursor is what
    stops a second round re-announcing the first round's sources."""
    run = await seed_run(database, worker_config)
    harness = build_worker(
        database, artifact_store, worker_config, script=Script(sufficient_on_round=3)
    )

    await run_one(harness, database, run.id)

    events = await streamed(database, run.id)
    sources = [event.payload["source_id"] for event in events if event.type is E.SOURCE_FOUND]
    claims = [event.payload["claim_id"] for event in events if event.type is E.CLAIM_EXTRACTED]
    assert len(sources) == len(set(sources)) > 0
    assert len(claims) == len(set(claims)) > 0


# --- restarts and failures ------------------------------------------------


async def test_a_resumed_run_continues_the_stream_rather_than_restarting_it(
    database, artifact_store, worker_config
):
    """A replacement process inherits the durable log and nothing else.

    The first worker is stopped mid-run; the second finishes it. What the
    reader must not see is the first half of the run narrated twice.
    """
    run = await seed_run(database, worker_config)
    first_worker = build_worker(
        database, artifact_store, worker_config, script=Script(hang={"critic"})
    )
    await first_worker.queue.enqueue(run.id)
    await serve_until(first_worker, status_is(database, run.id, RunStatus.VERIFYING))

    before = await streamed(database, run.id)
    assert E.SOURCE_FOUND in types(before), "the first worker got far enough to narrate"
    second_worker = respawn(first_worker, database, artifact_store, nodes=ScriptedNodes(Script()))
    # The status to wait for is explicit: the run is already `paused`, so
    # waiting for "terminal or paused" would be satisfied before the second
    # worker had looked at it.
    await run_one(second_worker, database, run.id, RunStatus.COMPLETED)

    after = await streamed(database, run.id)
    assert [event.seq for event in after] == list(range(1, len(after) + 1))
    # Everything the first worker said is still said exactly once.
    for event in before:
        if event.type is E.SOURCE_FOUND:
            matching = [
                later
                for later in after
                if later.type is E.SOURCE_FOUND
                and later.payload["source_id"] == event.payload["source_id"]
            ]
            assert len(matching) == 1, "a resumed run re-announced a source"
    assert after[-1].type is E.REPORT_COMPLETED


async def test_a_run_that_fails_for_good_says_so_on_the_stream(
    database, artifact_store, worker_config
):
    run = await seed_run(database, worker_config)
    harness = build_worker(
        database,
        artifact_store,
        worker_settings(worker_config, worker_max_attempts=1),
        script=Script(fail_on={"planner": {1}}),
    )

    await run_one(harness, database, run.id, RunStatus.FAILED)

    events = await streamed(database, run.id)
    failure = first(events, E.RESEARCH_FAILED)
    assert failure.status is RunStatus.FAILED
    assert failure.payload["partial_report"] is False
    assert failure.payload["code"]
    assert events[-1].type is E.RESEARCH_FAILED


async def test_a_cancelled_run_is_not_told_to_stop_twice(database, artifact_store, worker_config):
    """The API publishes `research_cancelled` when the user asks.

    The worker then observes the run stopping and would announce it again -
    the emitter's cursor was built before the cancellation existed, so the
    check has to ask the log rather than trust what it remembered.
    """
    run = await seed_run(database, worker_config)
    harness = build_worker(database, artifact_store, worker_config, script=Script(hang={"critic"}))
    await harness.queue.enqueue(run.id)
    await serve_until(harness, status_is(database, run.id, RunStatus.VERIFYING))

    # What the API does on POST /research/{id}/cancel: the row, then the event.
    await set_row(database, run.id, status=RunStatus.CANCELLED.value, completed_at=utcnow())
    await harness.broker.publish(
        EventDraft(
            type=E.RESEARCH_CANCELLED,
            run_id=run.id,
            status=RunStatus.CANCELLED,
            payload={"cancelled_by": "user"},
        )
    )
    resumed = respawn(harness, database, artifact_store, nodes=ScriptedNodes(Script()))
    await resumed.queue.enqueue(run.id)
    await serve_until(resumed, drained(resumed))

    cancellations = [
        event for event in await streamed(database, run.id) if event.type is E.RESEARCH_CANCELLED
    ]
    assert len(cancellations) == 1
    assert cancellations[0].payload["cancelled_by"] == "user"


async def test_a_run_paused_for_a_retry_is_not_reported_as_failed(
    database, artifact_store, worker_config
):
    """`paused` means resumable. A terminal event would close the stream on a
    run that is about to continue."""
    run = await seed_run(database, worker_config)
    harness = build_worker(
        database,
        artifact_store,
        worker_settings(worker_config, worker_max_attempts=3),
        script=Script(fail_on={"planner": {1}}),
    )

    await run_one(harness, database, run.id, RunStatus.PAUSED)

    assert E.RESEARCH_FAILED not in types(await streamed(database, run.id))


# --- the answer, as it is written -----------------------------------------


async def test_the_answer_arrives_in_pieces_that_reassemble_into_the_whole(
    database, artifact_store, settings
):
    """The claim this feature makes: a reader watches the answer appear.

    Both halves are asserted, because either alone would let the wrong thing
    ship. More than one delta proves it was streamed rather than posted at the
    end; the pieces joining back into `answer_completed`'s text proves the
    reader was watching the real answer rather than a summary of it.

    The chunk size is pinned rather than left at its default, and that is the
    whole point of this version of the test. The first one left it alone and
    asserted more than one delta - which held on a slow developer machine,
    where the quarter-second staleness flush fired *between* two writes of a
    scripted answer too short to fill the buffer, and failed on CI, where the
    same two writes land instantly. It was asserting that the machine was slow.
    Eight characters makes every write its own piece, so what is under test is
    the batching rule rather than the speed of the runner.
    """
    config = worker_settings(settings, answer_stream_chunk_chars=8)
    run = await seed_run(database, config)
    harness = build_worker(database, artifact_store, config)

    await run_one(harness, database, run.id)

    events = await streamed(database, run.id)
    deltas = [event for event in events if event.type is E.ANSWER_DELTA]
    assert len(deltas) > 1, "an answer delivered in one piece was not streamed"

    assert [event.payload["index"] for event in deltas] == list(range(1, len(deltas) + 1))

    completed = first(events, E.ANSWER_COMPLETED)
    assert "".join(event.payload["text"] for event in deltas) == completed.payload["text"]
    assert completed.payload["word_count"] > 0
    assert completed.payload["truncated"] is False


async def test_an_answer_too_short_to_fill_the_buffer_is_still_delivered(
    database, artifact_store, settings
):
    """The other half of the batching rule, and the one CI caught.

    With a chunk size the whole answer never reaches and a staleness window that
    will not fire inside the run, nothing flushes until the node closes the
    stream. The answer still arrives - in one piece, at `finish` - which is what
    stops a short answer being streamed as nothing at all.

    This is the exact condition that made the sibling test above fail on CI and
    pass here: two scripted writes landing inside the staleness window on a fast
    runner. Pinned deliberately so the behaviour is a decision rather than a
    property of whichever machine ran the suite.
    """
    config = worker_settings(
        settings, answer_stream_chunk_chars=100_000, answer_stream_max_delay_seconds=3600.0
    )
    run = await seed_run(database, config)
    harness = build_worker(database, artifact_store, config)

    await run_one(harness, database, run.id)

    events = await streamed(database, run.id)
    deltas = [event for event in events if event.type is E.ANSWER_DELTA]
    assert len(deltas) == 1
    completed = first(events, E.ANSWER_COMPLETED)
    assert deltas[0].payload["text"] == completed.payload["text"]
    # And the reader was told an answer was starting, exactly once, with it.
    assert types(events).count(E.ANSWER_STARTED) == 1


async def test_the_answer_is_announced_complete_exactly_once(
    database, artifact_store, worker_config
):
    """Even across a repaired report, which is synthesised and checked twice.

    The repair loop rewrites the report, never the answer, so a second
    `answer_completed` would tell a client to replace text that did not change.
    """
    run = await seed_run(database, worker_config)
    harness = build_worker(
        database, artifact_store, worker_config, script=Script(citation_failures=1)
    )

    await run_one(harness, database, run.id)

    emitted = types(await streamed(database, run.id))
    assert emitted.count(E.ANSWER_COMPLETED) == 1
    assert emitted.count(E.ANSWER_STARTED) == 1
    # The report really was written twice - otherwise this proves nothing.
    assert emitted.count(E.SYNTHESIS_STARTED) == 2


async def test_a_run_with_nothing_to_answer_from_streams_no_answer(
    database, artifact_store, worker_config
):
    """No claims, no answer, and no event claiming otherwise."""
    run = await seed_run(database, worker_config)
    harness = build_worker(
        database, artifact_store, worker_config, script=Script(answer_is_empty=True)
    )

    await run_one(harness, database, run.id)

    emitted = types(await streamed(database, run.id))
    assert E.ANSWER_COMPLETED not in emitted
    assert E.ANSWER_DELTA not in emitted
