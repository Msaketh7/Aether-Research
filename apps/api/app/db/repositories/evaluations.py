"""Benchmark results as rows, and the page that reads them back (Phase 18).

One `evaluations` row per case, carrying the metrics that were measured, the
thresholds that were in force, and the dataset version and commit they were
measured against. Storing all four is what makes a moved number attributable -
to the code, to the data, or to a moved goalpost - and storing the thresholds
*with* the result is what stops a later edit turning a past failure into a
pass.

**An absent metric is absent.** A metric that was not measured is left out of
the stored `metrics` object rather than written as zero. `/evaluations` then
reports it as unmeasured, which is what ``docs/evaluation.md`` requires and
what the page already renders.

**A benchmark run is reconstructed, not stored twice.** There is no
`benchmark_runs` table: a run *is* its rows, grouped by commit and dataset
version. One less table to keep in step, and the grouping is exactly how a
person would ask the question.

**Aggregation happens in Python, deliberately.** Averaging a jsonb object's
keys in SQL is possible and unreadable, and the thing being averaged is
"skip the metrics nobody measured" - a rule this codebase states in one place
(``app.evaluations.metrics``) and should not restate in a query. Both reads
are bounded: a dataset is small by design, and the bound is declared here
rather than discovered in production.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import Select, func, or_, select, tuple_

from app.core.enums import EvaluationKind
from app.db.models.evaluation import EvaluationRow
from app.db.session import Database
from app.evaluations.runner import BenchmarkResult
from app.evaluations.schemas import (
    EvaluationCaseResult,
    EvaluationRun,
    EvaluationsResponse,
    metric_values,
)

#: Benchmark runs the history chart shows, newest first.
MAX_HISTORY = 20
#: Case rows one response reads across that history.
MAX_ROWS = 2000


class SqlAlchemyEvaluationStore:
    """Writes benchmark results, and serves `/evaluations` from them."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def record(self, result: BenchmarkResult) -> None:
        """One row per case, in one transaction.

        All or nothing: half a benchmark stored is a result that reads as a
        complete run with cases missing, which is worse than no result.
        """
        gates = result.thresholds.gated()
        async with self._database.session() as session:
            for outcome in result.cases:
                session.add(
                    EvaluationRow(
                        run_id=None,
                        benchmark_id=outcome.case.id,
                        dataset_version=result.dataset_version,
                        git_sha=result.git_sha,
                        kind=result.kind.value,
                        metrics=_measured(outcome.metrics)
                        | {
                            # Prefixed, so the averaging below can skip what is
                            # not a metric without a list to maintain.
                            "_question": outcome.case.question,
                            "_checks": outcome.case.checks,
                            "_duration_seconds": outcome.duration_seconds,
                            "_failures": list(outcome.failures),
                        },
                        thresholds=dict(gates),
                        passed=outcome.passed,
                        created_at=result.completed_at,
                    )
                )

    async def latest(self) -> EvaluationsResponse:
        """The most recent benchmark, its cases, and the history behind it.

        ``latest`` is ``None`` when nothing has ever run, which the page
        renders as "no benchmark has been executed" rather than as an empty
        chart of zeros.
        """
        async with self._database.session() as session:
            groups = (await session.execute(self._groups_query())).all()
            if not groups:
                return EvaluationsResponse(latest=None, history=[], cases=[])
            keys = [(group.git_sha, group.dataset_version) for group in groups]
            rows = (await session.execute(self._rows_query(keys))).scalars().all()

        by_key: dict[tuple[str, str], list[EvaluationRow]] = defaultdict(list)
        for row in rows:
            by_key[(row.git_sha, row.dataset_version)].append(row)

        history = [
            _run_of(group, by_key[(group.git_sha, group.dataset_version)]) for group in groups
        ]
        newest = history[0]
        if len(history) > 1:
            newest = _with_deltas(newest, history[1])
        current = sorted(
            by_key[(groups[0].git_sha, groups[0].dataset_version)],
            key=lambda row: row.benchmark_id,
        )
        return EvaluationsResponse(
            latest=newest,
            history=history,
            cases=[_case_of(row) for row in current],
        )

    # --- the queries ------------------------------------------------------

    def _groups_query(self) -> Select[Any]:
        """One row per benchmark run, newest first. Plain aggregates only."""
        return (
            select(
                EvaluationRow.git_sha,
                EvaluationRow.dataset_version,
                func.min(EvaluationRow.kind).label("kind"),
                func.min(EvaluationRow.created_at).label("started_at"),
                func.max(EvaluationRow.created_at).label("finished_at"),
                func.count().label("cases"),
                func.count().filter(EvaluationRow.passed.is_(True)).label("passed"),
            )
            .group_by(EvaluationRow.git_sha, EvaluationRow.dataset_version)
            .order_by(func.max(EvaluationRow.created_at).desc())
            .limit(MAX_HISTORY)
        )

    def _rows_query(self, keys: list[tuple[str, str]]) -> Select[tuple[EvaluationRow]]:
        """Every case row belonging to those benchmark runs, bounded."""
        if not keys:  # pragma: no cover - the caller returns early
            return select(EvaluationRow).where(or_(False))
        return (
            select(EvaluationRow)
            .where(tuple_(EvaluationRow.git_sha, EvaluationRow.dataset_version).in_(keys))
            .order_by(EvaluationRow.created_at.desc(), EvaluationRow.benchmark_id)
            .limit(MAX_ROWS)
        )


def _measured(metrics: dict[str, float | None]) -> dict[str, float]:
    """Drop what was not measured. An absent metric is absent, not zero."""
    return {key: value for key, value in metrics.items() if value is not None}


def _run_of(group: Any, rows: list[EvaluationRow]) -> EvaluationRun:
    """One grouped benchmark as the page's `EvaluationRun`."""
    thresholds = dict(rows[0].thresholds or {}) if rows else {}
    return EvaluationRun(
        # Derived from the group's identity, so two reads of one benchmark
        # produce the same id rather than a new one each time.
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"{group.git_sha}/{group.dataset_version}"),
        dataset_version=group.dataset_version,
        git_sha=group.git_sha,
        kind=EvaluationKind(group.kind),
        model_config_used={},
        started_at=group.started_at,
        completed_at=group.finished_at,
        passed=int(group.passed) == int(group.cases),
        case_count=int(group.cases),
        passed_count=int(group.passed),
        metrics=metric_values(_mean(rows), thresholds=thresholds),
    )


def _with_deltas(run: EvaluationRun, previous: EvaluationRun) -> EvaluationRun:
    """The newest run's metrics, with a change against the one before it.

    Only where both sides were measured: "improved from nothing" is not a
    change, and rendering it as one would invent a trend.
    """
    before = {metric.key: metric.value for metric in previous.metrics}
    return run.model_copy(
        update={
            "metrics": [
                metric.model_copy(
                    update={
                        "delta": (
                            None
                            if metric.value is None or before.get(metric.key) is None
                            else round(metric.value - float(before[metric.key] or 0.0), 4)
                        )
                    }
                )
                for metric in run.metrics
            ]
        }
    )


def _mean(rows: list[EvaluationRow]) -> dict[str, float | None]:
    """Average each metric across the cases that measured it.

    A metric no case measured averages to ``None``, which is the whole point:
    an aggregate has to be able to say "nobody measured this" rather than
    reporting a zero somebody will read as a result.
    """
    keys = {key for row in rows for key in (row.metrics or {}) if not key.startswith("_")}
    summary: dict[str, float | None] = {}
    for key in sorted(keys):
        values = [
            float(row.metrics[key])
            for row in rows
            if isinstance((row.metrics or {}).get(key), int | float)
        ]
        summary[key] = round(sum(values) / len(values), 4) if values else None
    return summary


def _case_of(row: EvaluationRow) -> EvaluationCaseResult:
    metrics = {key: value for key, value in (row.metrics or {}).items() if not key.startswith("_")}
    return EvaluationCaseResult(
        case_id=row.benchmark_id,
        question=str(row.metrics.get("_question", "")),
        kind=EvaluationKind(row.kind),
        passed=row.passed,
        metrics=metric_values(metrics, thresholds=dict(row.thresholds or {})),
        failures=[str(item) for item in row.metrics.get("_failures", [])],
        run_id=row.run_id,
        duration_seconds=float(row.metrics.get("_duration_seconds", 0.0)),
    )
