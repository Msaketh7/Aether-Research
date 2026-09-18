"""The gate: which metrics have to hold, and what happens when one is absent.

Thresholds are configuration, not code (docs/evaluation.md §5), so a gate can
be ratcheted upward without a release - and so the values in force for a run
are stored *with* its result, which is what stops a later edit from turning a
past failure into a pass.

Three decisions worth stating.

**Only a metric that was measured can fail.** A gate applied to a ``None``
would fail every run against a partially labelled dataset, which teaches
everyone to ignore the gate. An unmeasured gated metric is reported as such
and the case is not failed on it - the dataset is what needs fixing, and the
report says so by name.

**Direction is declared, not inferred.** ``citation_precision`` has a floor
and ``unnecessary_source_rate`` has a ceiling. Guessing from the name is how a
gate ends up enforcing the opposite of what someone meant.

**They start where the first measurement lands.** Every default here is
``None`` - ungated - because this repository has executed no benchmark, and a
threshold written before a baseline is an aspiration presented as a
requirement. The rule the whole document rests on is that no number appears
unless the suite produced it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

type Direction = Literal["floor", "ceiling"]

#: Which way each gated metric is compared. A metric absent from this map
#: cannot be gated at all, which is deliberate: the set of things that can
#: fail a build is a decision, not a side effect of adding a measurement.
DIRECTIONS: dict[str, Direction] = {
    "citation_precision": "floor",
    "citation_recall": "floor",
    "groundedness": "floor",
    "claim_recall": "floor",
    "topic_coverage": "floor",
    "task_completion": "floor",
    "source_type_coverage": "floor",
    "unnecessary_source_rate": "ceiling",
    "cost_usd": "ceiling",
    "runtime_seconds": "ceiling",
}


class Thresholds(BaseModel):
    """The gate values in force. Every one defaults to ungated."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: A wrong citation is worse than a missing one, so this is the gate that
    #: is expected to be set first and highest - once there is a baseline.
    citation_precision: float | None = Field(default=None, ge=0.0, le=1.0)
    citation_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    groundedness: float | None = Field(default=None, ge=0.0, le=1.0)
    claim_recall: float | None = Field(default=None, ge=0.0, le=1.0)
    topic_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    task_completion: float | None = Field(default=None, ge=0.0, le=1.0)
    source_type_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    unnecessary_source_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    cost_usd: float | None = Field(default=None, gt=0.0)
    runtime_seconds: float | None = Field(default=None, gt=0.0)

    def gated(self) -> dict[str, float]:
        """The metrics that actually have a gate, in a stable order."""
        return {
            key: value
            for key, value in self.model_dump().items()
            if value is not None and key in DIRECTIONS
        }


@dataclass(frozen=True, slots=True)
class Verdict:
    """One metric against its gate."""

    metric: str
    threshold: float
    #: ``None`` when the metric was not measured, which is not a failure.
    value: float | None
    direction: Direction

    @property
    def measured(self) -> bool:
        return self.value is not None

    @property
    def passed(self) -> bool | None:
        """``None`` when there was nothing to compare - see the module notes."""
        if self.value is None:
            return None
        return (
            self.value >= self.threshold
            if self.direction == "floor"
            else (self.value <= self.threshold)
        )

    @property
    def explanation(self) -> str:
        if self.value is None:
            return f"{self.metric} was not measured, so its gate could not be applied."
        word = "at least" if self.direction == "floor" else "at most"
        return f"{self.metric} was {self.value:g}; the gate requires {word} {self.threshold:g}."


def evaluate(
    metrics: dict[str, float | None], thresholds: Thresholds
) -> tuple[bool, list[Verdict]]:
    """Whether this result passes, and the verdict on every gated metric.

    Passes when nothing gated *failed*. A run whose gated metrics were all
    unmeasured therefore passes, and the verdicts say why - a build that went
    red because a dataset has no labels would be a build nobody trusts.
    """
    verdicts = [
        Verdict(
            metric=metric,
            threshold=threshold,
            value=metrics.get(metric),
            direction=DIRECTIONS[metric],
        )
        for metric, threshold in thresholds.gated().items()
    ]
    return all(verdict.passed is not False for verdict in verdicts), verdicts
