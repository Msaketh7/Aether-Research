"""The load test's arithmetic and its honesty rules (Phase 21).

The apparatus that produces a performance number has to be trustworthy before
the number is worth anything, and almost every way of getting this wrong
produces a plausible figure rather than an error. So the assertions here are
arithmetic with a known answer, plus one class of behaviour that matters more
than the arithmetic: **nothing absent is ever reported as zero**. A profile in
which no run completed has no latency distribution; a level nothing could read
has no maximum; a percentile of an empty sequence does not exist. Each of
those is ``None`` here and prints as ``-`` in the report, and each would be a
lie as ``0.0``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid

import pytest

from app.core.enums import RunStatus
from app.loadtest.measure import Sample, Sampler, percentile, summarise, timeline_of
from app.loadtest.profiles import DEFAULT_PROFILES, LoadProfile, profile_named
from app.loadtest.report import render_markdown
from app.loadtest.results import LoadResult, LoadSuite, RunOutcome
from app.models.limiter import ConcurrencyLimiter

EPOCH = dt.datetime(2026, 3, 1, 12, 0, tzinfo=dt.UTC)


def at(seconds: float) -> dt.datetime:
    return EPOCH + dt.timedelta(seconds=seconds)


def outcome(
    *,
    status: RunStatus = RunStatus.COMPLETED,
    created: float = 0.0,
    started: float | None = 1.0,
    completed: float | None = 5.0,
) -> RunOutcome:
    return RunOutcome(
        run_id=uuid.uuid4(),
        status=status,
        created_at=at(created),
        started_at=None if started is None else at(started),
        completed_at=None if completed is None else at(completed),
    )


def result(
    outcomes: tuple[RunOutcome, ...],
    *,
    offered: int,
    wall: float = 60.0,
    samples: tuple[Sample, ...] = (),
) -> LoadResult:
    profile = LoadProfile(name=f"offered-{offered}", offered_jobs=offered, worker_concurrency=2)
    return LoadResult(
        profile=profile,
        started_at=EPOCH,
        wall_seconds=wall,
        outcomes=outcomes,
        timeline=timeline_of(samples, interval=0.25, capacity=profile.capacity),
        saturation=None,
    )


# --- percentiles ----------------------------------------------------------


def test_percentile_interpolates_between_the_neighbouring_samples():
    """P50 of 1..4 is 2.5 - between the two middle values, not one of them."""
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.50) == pytest.approx(2.5)


def test_percentile_endpoints_are_the_extremes():
    assert percentile([5.0, 1.0, 3.0], 0.0) == 1.0
    assert percentile([5.0, 1.0, 3.0], 1.0) == 5.0


def test_percentile_of_one_sample_is_that_sample():
    """Ten runs is a declared profile, so the small-n case is not hypothetical."""
    assert percentile([7.5], 0.99) == 7.5


def test_percentile_of_nothing_raises_rather_than_returning_zero():
    """Zero is a latency. Handing one back for no data would fabricate a number."""
    with pytest.raises(ValueError, match="does not exist"):
        percentile([], 0.95)


def test_percentile_refuses_a_fraction_outside_the_unit_interval():
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        percentile([1.0], 95.0)


def test_summarise_reports_none_for_an_empty_distribution():
    assert summarise([]) is None


def test_summarise_counts_every_value_it_was_given():
    latency = summarise([1.0, 2.0, 3.0])
    assert latency is not None
    assert (latency.count, latency.min, latency.max) == (3, 1.0, 3.0)
    assert latency.mean == pytest.approx(2.0)


# --- one run's timings ----------------------------------------------------


def test_a_run_that_never_started_has_no_queue_wait():
    """Not zero: it waited, and how long is unknown until a worker claims it."""
    never = outcome(started=None, completed=None)
    assert never.queue_wait_seconds is None
    assert never.execution_seconds is None
    assert never.total_seconds is None


def test_timings_split_the_wait_from_the_work():
    one = outcome(created=0.0, started=2.0, completed=9.0)
    assert one.queue_wait_seconds == pytest.approx(2.0)
    assert one.execution_seconds == pytest.approx(7.0)
    assert one.total_seconds == pytest.approx(9.0)


# --- a profile's result ---------------------------------------------------


def test_completion_rate_is_against_what_was_offered_not_what_was_observed():
    """A run that never got a row is a run the system lost, and must count."""
    measured = result((outcome(), outcome()), offered=10)
    assert measured.completed == 2
    assert measured.completion_rate == pytest.approx(0.2)


def test_failed_runs_are_excluded_from_the_latency_distribution():
    """A run that failed in a second is not a fast run.

    Letting it in would make a system that starts failing under load report
    improving percentiles, which is the exact shape of a load test that
    reassures everybody on the way to an outage.
    """
    measured = result(
        (
            outcome(created=0.0, started=0.0, completed=10.0),
            outcome(status=RunStatus.FAILED, created=0.0, started=0.0, completed=1.0),
        ),
        offered=2,
    )
    latency = measured.total_latency
    assert latency is not None
    assert latency.count == 1
    assert latency.p50 == pytest.approx(10.0)


def test_a_profile_where_nothing_completed_has_no_latency_at_all():
    measured = result((outcome(status=RunStatus.FAILED, completed=3.0),), offered=4)
    assert measured.total_latency is None
    assert measured.completion_rate == pytest.approx(0.0)


def test_throughput_counts_completed_runs_over_the_whole_wall_clock():
    measured = result(tuple(outcome() for _ in range(6)), offered=6, wall=120.0)
    assert measured.throughput_per_minute == pytest.approx(3.0)


def test_by_status_reports_every_terminal_state_the_runs_reached():
    measured = result(
        (outcome(), outcome(status=RunStatus.FAILED), outcome(status=RunStatus.CANCELLED)),
        offered=3,
    )
    assert measured.by_status == {"completed": 1, "failed": 1, "cancelled": 1}


# --- the sampled levels ---------------------------------------------------


def sample(at_seconds: float, *, depth: int | None, running: int, pool: int | None = 3) -> Sample:
    return Sample(
        at=at_seconds,
        queue_depth=depth,
        running=running,
        db_in_use=pool,
        db_idle=None,
        db_overflow=None,
        llm_in_flight=None,
    )


def test_a_level_nothing_could_read_has_no_series():
    """`db_idle` was never readable here, so it is absent rather than zero."""
    timeline = timeline_of(
        (sample(0.0, depth=2, running=1), sample(0.25, depth=1, running=2)),
        interval=0.25,
        capacity=2,
    )
    assert timeline.db_idle is None
    assert timeline.queue_depth is not None
    assert timeline.queue_depth.max == 2.0


def test_saturation_is_the_share_of_samples_with_every_slot_busy():
    timeline = timeline_of(
        (
            sample(0.0, depth=4, running=2),
            sample(0.25, depth=3, running=2),
            sample(0.5, depth=0, running=1),
            sample(0.75, depth=0, running=1),
        ),
        interval=0.25,
        capacity=2,
    )
    assert timeline.saturated_fraction == pytest.approx(0.5)


def test_a_timeline_with_no_samples_reports_nothing_rather_than_zeros():
    timeline = timeline_of((), interval=0.25, capacity=4)
    assert timeline.samples == 0
    assert timeline.running is None
    assert timeline.saturated_fraction is None


async def test_the_sampler_keeps_reading_after_a_reading_fails():
    """One unreadable level must leave a gap, not end the measurement."""
    attempts = {"n": 0}
    enough = asyncio.Event()

    async def probe(at_seconds: float) -> Sample:
        attempts["n"] += 1
        if attempts["n"] >= 4:
            enough.set()
        if attempts["n"] == 2:
            raise RuntimeError("the pool was mid-resize")
        return sample(at_seconds, depth=1, running=1)

    async with Sampler(probe=probe, interval_seconds=0.01) as sampler:
        await asyncio.wait_for(enough.wait(), timeout=5)

    # One reading short of the attempts: the second one raised, and a reading
    # that could not be taken is a gap rather than a fabricated sample.
    assert len(sampler.samples) == attempts["n"] - 1


# --- the declared profiles ------------------------------------------------


def test_the_declared_profiles_are_the_four_loads_the_build_plan_names():
    """10, 25, 50 and 100 concurrent research jobs, from `docs/PHASES.md`.

    Here as a test rather than as prose so that quietly dropping the hundred -
    the only profile long enough to be tempting to drop - fails the build.
    """
    assert [profile.offered_jobs for profile in DEFAULT_PROFILES] == [10, 25, 50, 100]


def test_every_declared_profile_offers_more_than_it_can_run_at_once():
    """Otherwise there is no backlog, and queue depth measures nothing."""
    for profile in DEFAULT_PROFILES:
        assert profile.queued_at_peak > 0, profile.name


def test_a_profile_with_no_jobs_or_no_capacity_is_refused():
    with pytest.raises(ValueError, match="measures nothing"):
        LoadProfile(name="empty", offered_jobs=0)
    with pytest.raises(ValueError, match="never finish"):
        LoadProfile(name="stuck", offered_jobs=4, worker_concurrency=0)


def test_an_unknown_profile_names_the_ones_that_exist():
    with pytest.raises(KeyError, match="offered-10"):
        profile_named("offered-7")


# --- the report -----------------------------------------------------------


def suite() -> LoadSuite:
    measured = result((outcome(), outcome(status=RunStatus.FAILED)), offered=4)
    return LoadSuite(
        results=(measured,),
        conditions={"what is scripted": "The model provider and the socket."},
        skipped={"offered-100": "the deadline was reached"},
    )


def test_the_report_prints_the_conditions_beside_the_numbers():
    """A throughput figure without its conditions is not a fact about anything."""
    rendered = render_markdown(suite())
    assert "The model provider and the socket." in rendered


def test_the_report_prints_an_unmeasured_cell_as_a_dash():
    """`saturation` is None here: this profile ran no gateway that reports it."""
    rendered = render_markdown(suite())
    throttling = rendered.split("## Model-call throttling")[1].split("##")[0]
    assert "| offered-4 | - | - | - | - | - |" in throttling


def test_the_report_names_the_profiles_that_did_not_run():
    assert "profile offered-100" in render_markdown(suite())


def test_a_suite_with_no_results_says_so_rather_than_printing_empty_tables():
    rendered = render_markdown(LoadSuite(results=(), conditions={}))
    assert "No profile produced a measurement." in rendered


# --- the gateway's throttle ------------------------------------------------


async def test_an_uncontended_slot_is_recorded_as_a_zero_wait_not_omitted():
    """Every acquisition is counted, so the mean has the right denominator.

    A histogram fed only the calls that queued reports a healthy median while
    the system is queueing, because the ones that did not queue are missing
    from the bottom of the distribution.
    """
    observed: list[float] = []
    limiter = ConcurrencyLimiter(2, observer=observed.append)
    async with limiter.slot():
        pass
    saturation = limiter.saturation
    assert (saturation.acquisitions, saturation.waited) == (1, 0)
    assert observed == [0.0]


async def test_a_call_that_queued_behind_the_ceiling_has_its_wait_measured():
    limiter = ConcurrencyLimiter(1)
    held = asyncio.Event()

    async def first() -> None:
        async with limiter.slot():
            held.set()
            await asyncio.sleep(0.05)

    async def second() -> None:
        await held.wait()
        async with limiter.slot():
            pass

    await asyncio.gather(first(), second())
    saturation = limiter.saturation
    assert saturation.acquisitions == 2
    assert saturation.waited == 1
    assert saturation.max_wait_seconds > 0.0
    assert saturation.max_in_flight == 1


async def test_the_high_water_mark_is_what_the_ceiling_actually_allowed():
    limiter = ConcurrencyLimiter(3)
    release = asyncio.Event()
    full = asyncio.Event()

    async def hold() -> None:
        async with limiter.slot():
            if limiter.in_flight == limiter.limit:
                full.set()
            await release.wait()

    tasks = [asyncio.create_task(hold()) for _ in range(3)]
    await asyncio.wait_for(full.wait(), timeout=5)
    assert limiter.saturation.utilisation == pytest.approx(1.0)
    release.set()
    await asyncio.gather(*tasks)
    assert limiter.in_flight == 0
    assert limiter.saturation.max_in_flight == 3


async def test_a_slot_is_released_when_the_call_inside_it_raises():
    """Otherwise one provider error would permanently shrink the ceiling."""
    limiter = ConcurrencyLimiter(1)
    with pytest.raises(RuntimeError):
        async with limiter.slot():
            raise RuntimeError("the provider refused")
    assert limiter.in_flight == 0
    async with limiter.slot():
        pass  # would deadlock if the slot had leaked


async def test_an_observer_that_raises_does_not_fail_the_call():
    """A metric must never take down a research run."""

    def explode(_waited: float) -> None:
        raise ValueError("the registry is in a strange state")

    limiter = ConcurrencyLimiter(1, observer=explode)
    async with limiter.slot():
        pass
    assert limiter.saturation.acquisitions == 1


def test_a_limit_below_one_is_refused():
    with pytest.raises(ValueError, match="admit nothing"):
        ConcurrencyLimiter(0)
