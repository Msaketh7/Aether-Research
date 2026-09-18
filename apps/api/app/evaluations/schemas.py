"""What an evaluation run reports (Phase 18).

Mirrors `EvaluationRun`, `EvaluationCaseResult` and `MetricValue` in
``@aether/shared-types``, which the `/evaluations` page has been built against
since Phase 1. One property of that contract is the whole point of this
module: **``value`` is nullable, and null means the metric was not measured.**
The page renders that as "not yet measured" and never as a zero, because the
two mean opposite things and a benchmark that cannot say which is a benchmark
nobody should act on.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.core.enums import EvaluationKind
from app.research.schemas import ApiModel

#: How a metric renders. `ratio` is shown as a percentage; the rest literally.
type MetricUnit = Literal["ratio", "seconds", "usd", "count"]

#: Which unit each metric carries, so the page does not have to guess from a
#: name. A metric missing from here renders as a count.
UNITS: dict[str, MetricUnit] = {
    "topic_coverage": "ratio",
    "task_completion": "ratio",
    "source_type_coverage": "ratio",
    "unnecessary_source_rate": "ratio",
    "claim_recall": "ratio",
    "evidence_per_claim": "count",
    "citation_precision": "ratio",
    "citation_recall": "ratio",
    "groundedness": "ratio",
    "contradictions_found": "count",
    "cost_usd": "usd",
    "runtime_seconds": "seconds",
}

#: Human labels, so the page reads as English rather than as field names.
LABELS: dict[str, str] = {
    "topic_coverage": "Topic coverage",
    "task_completion": "Task completion",
    "source_type_coverage": "Source types reached",
    "unnecessary_source_rate": "Sources never cited",
    "claim_recall": "Expected claims found",
    "evidence_per_claim": "Evidence per claim",
    "citation_precision": "Citation precision",
    "citation_recall": "Citation recall",
    "groundedness": "Groundedness",
    "contradictions_found": "Contradictions found",
    "cost_usd": "Cost",
    "runtime_seconds": "Runtime",
}


class MetricValue(ApiModel):
    """One measured metric, with its gate and how it moved."""

    key: str
    label: str
    #: ``None`` is *not measured*. Never substituted with 0.
    value: float | None
    unit: MetricUnit
    #: The gate in force, or ``None`` when this metric is not gated.
    threshold: float | None = None
    #: ``None`` when either side is unmeasured, which is not a failure.
    passed: bool | None = None
    #: Change against the previous benchmark run, or ``None`` when there is none.
    delta: float | None = None


class EvaluationCaseResult(ApiModel):
    """One case's result."""

    case_id: str
    question: str
    kind: EvaluationKind
    passed: bool
    metrics: list[MetricValue]
    #: Why it failed, in plain language. Empty when it passed.
    failures: list[str] = Field(default_factory=list)
    run_id: UUID | None = None
    duration_seconds: float


class EvaluationRun(ApiModel):
    """One execution of a dataset against one build."""

    id: UUID
    dataset_version: str
    git_sha: str
    kind: EvaluationKind
    #: The model routing in force, so a result is attributable to a build.
    model_config_used: dict[str, str] = Field(
        default_factory=dict, serialization_alias="model_config"
    )
    started_at: datetime
    completed_at: datetime | None
    passed: bool
    case_count: int
    passed_count: int
    metrics: list[MetricValue]


class EvaluationsResponse(ApiModel):
    """What `/evaluations` returns.

    ``latest`` is ``None`` when no benchmark has ever run, and the page says
    so rather than rendering an empty chart of zeros.
    """

    latest: EvaluationRun | None = None
    history: list[EvaluationRun] = Field(default_factory=list)
    cases: list[EvaluationCaseResult] = Field(default_factory=list)


def metric_values(
    metrics: dict[str, float | None],
    *,
    thresholds: dict[str, float] | None = None,
    previous: dict[str, float | None] | None = None,
) -> list[MetricValue]:
    """Turn a measured dictionary into the shape the page renders.

    Ordered by ``UNITS`` rather than by the dictionary, so two runs' panels
    line up; and a delta is computed only when *both* sides were measured,
    because "improved from nothing" is not a change.
    """
    gates = thresholds or {}
    before = previous or {}
    values: list[MetricValue] = []
    for key in UNITS:
        if key not in metrics:
            continue
        value = metrics[key]
        threshold = gates.get(key)
        was = before.get(key)
        values.append(
            MetricValue(
                key=key,
                label=LABELS.get(key, key.replace("_", " ").capitalize()),
                value=value,
                unit=UNITS[key],
                threshold=threshold,
                passed=_passed(key, value, threshold),
                delta=None if value is None or was is None else round(value - was, 4),
            )
        )
    return values


def _passed(key: str, value: float | None, threshold: float | None) -> bool | None:
    from app.evaluations.thresholds import DIRECTIONS

    if value is None or threshold is None:
        return None
    return value >= threshold if DIRECTIONS.get(key, "floor") == "floor" else value <= threshold
