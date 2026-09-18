"""The suite as Markdown, for a document a person reads rather than a JSON file.

One table per question the build plan asks, with the profiles as rows, so that
the shape of the degradation is visible: a throughput column that stops rising
and a queue-depth column that starts rising are the same sentence said twice,
and seeing them side by side is the whole point of running four loads instead
of one.

Every cell that was not measured prints as ``-`` and is explained underneath.
There is no cell in this report that was computed from anything other than an
observation.
"""

from __future__ import annotations

from app.loadtest import breakdown as node_breakdown
from app.loadtest.measure import Latency, Series
from app.loadtest.results import LoadResult, LoadSuite

DASH = "-"


def _cell(value: float | int | None, places: int = 2) -> str:
    if value is None:
        return DASH
    if isinstance(value, int):
        return str(value)
    return f"{value:.{places}f}"


def _series_cell(series: Series | None, places: int = 1) -> str:
    return DASH if series is None else f"{series.max:.{places}f}"


def _latency_row(name: str, latency: Latency | None) -> str:
    if latency is None:
        return f"| {name} | {DASH} | {DASH} | {DASH} | {DASH} | {DASH} |"
    return (
        f"| {name} | {latency.count} | {latency.p50:.2f} | "
        f"{latency.p95:.2f} | {latency.p99:.2f} | {latency.max:.2f} |"
    )


def _table(header: str, rows: list[str]) -> list[str]:
    columns = [part.strip() for part in header.strip("|").split("|")]
    divider = "|" + "|".join(" --- " for _ in columns) + "|"
    return [header, divider, *rows, ""]


def _throughput(results: tuple[LoadResult, ...]) -> list[str]:
    rows = [
        (
            f"| {result.profile.name} | {result.offered} | {result.profile.capacity} | "
            f"{result.wall_seconds:.1f} | {result.completed} | "
            f"{result.completion_rate * 100:.1f}% | "
            f"{result.throughput_per_minute:.2f} |"
        )
        for result in results
    ]
    return _table(
        "| profile | offered | capacity | wall (s) | completed | completion rate | runs/min |",
        rows,
    )


def _latencies(result: LoadResult) -> list[str]:
    rows = [
        _latency_row("total (submit to finish)", result.total_latency),
        _latency_row("queue wait", result.queue_latency),
        _latency_row("execution", result.execution_latency),
    ]
    return _table("| stage | n | P50 (s) | P95 (s) | P99 (s) | max (s) |", rows)


def _levels(results: tuple[LoadResult, ...]) -> list[str]:
    rows = []
    for result in results:
        timeline = result.timeline
        utilisation = timeline.saturated_fraction
        rows.append(
            f"| {result.profile.name} | {_series_cell(timeline.queue_depth)} | "
            f"{_series_cell(timeline.running)} | "
            f"{DASH if utilisation is None else f'{utilisation * 100:.0f}%'} | "
            f"{_series_cell(timeline.db_in_use)} | {_series_cell(timeline.db_overflow)} | "
            f"{_series_cell(timeline.llm_in_flight)} | {timeline.samples} |"
        )
    return _table(
        "| profile | peak queue depth | peak running | slots saturated | "
        "peak db in use | peak db overflow | peak llm in flight | samples |",
        rows,
    )


def _commits(results: tuple[LoadResult, ...]) -> list[str]:
    rows = [
        (
            f"| {result.profile.name} | {_cell(result.commits)} | "
            f"{_cell(result.commits_per_run, 1)} |"
        )
        for result in results
    ]
    return _table("| profile | commits | commits per run |", rows)


def _throttling(results: tuple[LoadResult, ...]) -> list[str]:
    rows = []
    for result in results:
        saturation = result.saturation
        if saturation is None:
            rows.append(f"| {result.profile.name} | {DASH} | {DASH} | {DASH} | {DASH} | {DASH} |")
            continue
        waited_share = (
            saturation.waited / saturation.acquisitions if saturation.acquisitions else None
        )
        rows.append(
            f"| {result.profile.name} | {saturation.limit} | {saturation.acquisitions} | "
            f"{DASH if waited_share is None else f'{waited_share * 100:.1f}%'} | "
            f"{_cell(saturation.mean_wait_seconds, 4)} | {_cell(saturation.max_wait_seconds, 3)} |"
        )
    return _table(
        "| profile | slot limit | model calls | calls that queued | mean wait (s) | max wait (s) |",
        rows,
    )


def render_markdown(suite: LoadSuite, *, title: str = "Load test results") -> str:
    """The whole suite as one document."""
    lines: list[str] = [f"# {title}", ""]

    lines += ["## Conditions", ""]
    lines += [f"- **{key}** - {value}" for key, value in suite.conditions.items()]
    lines += [""]

    if not suite.results:
        lines += ["No profile produced a measurement.", ""]
    else:
        lines += ["## Throughput and completion", ""]
        lines += _throughput(suite.results)

        lines += ["## Levels under load", ""]
        lines += [
            "Instantaneous readings at a fixed interval, so a peak shorter than "
            "the interval is a peak this did not see.",
            "",
        ]
        lines += _levels(suite.results)

        lines += ["## Database round trips", ""]
        lines += [
            "Transactions Postgres committed while the profile ran, counted by "
            "Postgres. A CPU profile cannot supply this: it counts every "
            "resumption of a coroutine as a call.",
            "",
        ]
        lines += _commits(suite.results)

        lines += ["## Model-call throttling", ""]
        lines += [
            "The gateway's own concurrency ceiling. A call that queued here was "
            "held by this system, not by a provider.",
            "",
        ]
        lines += _throttling(suite.results)

        lines += ["## Latency", ""]
        lines += [
            "Percentiles interpolate between order statistics. Failed runs are "
            "excluded: a run that failed quickly is not a fast run.",
            "",
        ]
        for result in suite.results:
            lines += [f"### {result.profile.name}", ""]
            lines += _latencies(result)

        if any(result.breakdown is not None for result in suite.results):
            lines += ["## Where the node time went", ""]
            lines += [
                "From the `agent_runs` rows each profile wrote, not from a "
                "sampler. Ranked by total, because a cheap node that runs eight "
                "times costs more than an expensive one that runs once.",
                "",
            ]
            for result in suite.results:
                if result.breakdown is None:
                    continue
                lines += [f"### {result.profile.name}", ""]
                lines += node_breakdown.render(result.breakdown)

    not_measured = {
        key: reason for result in suite.results for key, reason in result.not_measured.items()
    }
    if not_measured or suite.skipped:
        lines += ["## Not measured", ""]
        lines += [f"- **{key}** - {reason}" for key, reason in sorted(not_measured.items())]
        lines += [
            f"- **profile {name}** - not run: {reason}" for name, reason in suite.skipped.items()
        ]
        lines += [""]

    return "\n".join(lines)
