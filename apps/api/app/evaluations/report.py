"""The report a benchmark prints (Phase 18).

Plain text, because the audience is a person reading CI output and the other
audience - the product's `/evaluations` page - reads the rows instead. What
matters here is what is *shown*, and the rule is the document's: a metric that
was not measured says ``not measured``, never ``0.00``.

The header carries the dataset version, the commit and the model routing,
because a number without those three is not comparable to any other number.
"""

from __future__ import annotations

from app.evaluations.runner import BenchmarkResult
from app.evaluations.schemas import LABELS, UNITS
from app.evaluations.thresholds import DIRECTIONS

_WIDTH = 78
_NOT_MEASURED = "not measured"


def render(result: BenchmarkResult) -> str:
    """The whole benchmark as text."""
    gates = result.thresholds.gated()
    lines = [
        "=" * _WIDTH,
        f"Benchmark  {result.dataset_version}   commit {result.git_sha[:12]}",
        f"Ran        {len(result.cases)} cases in "
        f"{(result.completed_at - result.started_at).total_seconds():.1f}s",
        f"Models     {', '.join(sorted(result.model_config_used)) or 'none declared'}",
        f"Verdict    {'PASS' if result.passed else 'FAIL'} "
        f"({result.passed_count}/{len(result.cases)} cases passed)",
        "=" * _WIDTH,
        "",
        "Aggregate (mean of the cases that measured each metric)",
    ]
    lines += [f"  {line}" for line in _metrics(result.metrics, gates)]
    lines += ["", "Cases"]
    for outcome in result.cases:
        mark = "PASS" if outcome.passed else "FAIL"
        lines.append(f"  [{mark}] {outcome.case.id}  ({outcome.duration_seconds:.1f}s)")
        for failure in outcome.failures:
            lines.append(f"         - {failure}")
    if not gates:
        lines += [
            "",
            "No metric is gated. Thresholds start where the first measured "
            "baseline lands; a gate written before one is an aspiration, not a "
            "requirement (docs/evaluation.md).",
        ]
    return "\n".join(lines)


def _metrics(metrics: dict[str, float | None], gates: dict[str, float]) -> list[str]:
    lines: list[str] = []
    for key in UNITS:
        if key not in metrics:
            continue
        label = LABELS.get(key, key)
        lines.append(f"{label:<28} {_value(key, metrics[key]):>14}{_gate(key, gates)}")
    return lines


def _value(key: str, value: float | None) -> str:
    if value is None:
        return _NOT_MEASURED
    unit = UNITS.get(key, "count")
    if unit == "ratio":
        return f"{value * 100:.1f}%"
    if unit == "usd":
        return f"${value:.4f}"
    if unit == "seconds":
        return f"{value:.1f}s"
    return f"{value:g}"


def _gate(key: str, gates: dict[str, float]) -> str:
    threshold = gates.get(key)
    if threshold is None:
        return ""
    word = ">=" if DIRECTIONS.get(key) == "floor" else "<="
    return f"   (gate {word} {_value(key, threshold)})"
