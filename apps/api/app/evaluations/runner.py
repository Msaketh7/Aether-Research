"""Executing a dataset against a build, and scoring what came back (Phase 18).

The runner is deliberately thin. It owns three things - which cases to run,
how to turn a finished run into ``RunFacts``, and where the results go - and
delegates everything else: the research itself to the same graph runner the
worker uses, the scoring to pure functions, the gate to configuration.

**It runs the real thing.** A benchmark that exercised a special evaluation
path would measure that path. Each case goes through ``ResearchGraphRunner``
exactly as a queued run does, which also means a case costs what a run costs -
so the dataset is small and the runner is something someone starts on purpose.

**A case that fails is a result, not a crash.** A research run that raises is
scored as a failed case with its error recorded, and the rest of the dataset
continues. A benchmark that stops at the first failure tells you about one
case; the point is to learn about all of them.

**Nothing is invented.** Every metric is measured or absent, the aggregate
skips what was not measured rather than counting it as zero, and a run that
produced no report has no citation metrics rather than perfect ones. The rule
this repository states in ``docs/evaluation.md`` is the one the code enforces:
no number exists unless the suite produced it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from app.agents.runtime import ResearchGraphRunner
from app.agents.schemas import RunBudget
from app.agents.state import ResearchState, RunBrief
from app.core.enums import EvaluationKind
from app.core.logging import get_logger
from app.evaluations.dataset import REPO_ROOT, Dataset, EvaluationCase
from app.evaluations.metrics import RunFacts, aggregate, score_case
from app.evaluations.thresholds import Thresholds, evaluate
from app.research.schemas import RunLimits

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """One case, scored."""

    case: EvaluationCase
    metrics: dict[str, float | None]
    passed: bool
    failures: tuple[str, ...]
    run_id: uuid.UUID
    duration_seconds: float
    error: str | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """A whole dataset, scored, with the build it was measured against."""

    id: uuid.UUID
    dataset_version: str
    git_sha: str
    kind: EvaluationKind
    model_config_used: dict[str, str]
    started_at: dt.datetime
    completed_at: dt.datetime
    thresholds: Thresholds
    cases: tuple[CaseOutcome, ...] = field(default_factory=tuple)

    @property
    def metrics(self) -> dict[str, float | None]:
        return aggregate(outcome.metrics for outcome in self.cases)

    @property
    def passed_count(self) -> int:
        return sum(1 for outcome in self.cases if outcome.passed)

    @property
    def passed(self) -> bool:
        """The whole benchmark passes when every case did."""
        return all(outcome.passed for outcome in self.cases)


class EvaluationStore(Protocol):
    """Where results go. Implemented in ``app.db.repositories.evaluations``."""

    async def record(self, result: BenchmarkResult) -> None: ...


class BenchmarkRunner:
    """Runs a dataset and scores it."""

    def __init__(
        self,
        *,
        graph: ResearchGraphRunner,
        store: EvaluationStore,
        limits: RunLimits,
        thresholds: Thresholds | None = None,
        model_config_used: dict[str, str] | None = None,
    ) -> None:
        self._graph = graph
        self._store = store
        self._limits = limits
        self._thresholds = thresholds or Thresholds()
        self._models = model_config_used or {}

    async def run(
        self, dataset: Dataset, *, user_id: uuid.UUID, kind: EvaluationKind = EvaluationKind.FULL
    ) -> BenchmarkResult:
        """Execute every case, score it, store the result, return it."""
        started = dt.datetime.now(dt.UTC)
        outcomes = [await self._one(case, user_id=user_id) for case in dataset.cases]
        result = BenchmarkResult(
            id=uuid.uuid4(),
            dataset_version=dataset.version,
            git_sha=git_sha(),
            kind=kind,
            model_config_used=dict(self._models),
            started_at=started,
            completed_at=dt.datetime.now(dt.UTC),
            thresholds=self._thresholds,
            cases=tuple(outcomes),
        )
        await self._store.record(result)
        logger.info(
            "benchmark finished",
            extra={
                "dataset_version": dataset.version,
                "git_sha": result.git_sha,
                "cases": len(outcomes),
                "passed": result.passed_count,
            },
        )
        return result

    async def _one(self, case: EvaluationCase, *, user_id: uuid.UUID) -> CaseOutcome:
        run_id = uuid.uuid4()
        started = dt.datetime.now(dt.UTC)
        brief = RunBrief(
            research_id=run_id,
            user_id=user_id,
            query=case.question,
            mode=case.mode,
            depth=1 if case.mode.value == "quick" else 3,
            budget=RunBudget.from_limits(self._limits),
        )
        error: str | None = None
        try:
            state = await self._graph.run(brief)
        except Exception as exc:
            # A case that failed is a result. The next one still runs.
            error = f"{type(exc).__name__}: {exc}"[:500]
            logger.warning("a benchmark case failed", extra={"case": case.id, "error": error})
            state = ResearchState()

        duration = (dt.datetime.now(dt.UTC) - started).total_seconds()
        facts = facts_of(state, duration_seconds=duration, failed=error is not None)
        metrics = score_case(case, facts)
        passed, verdicts = evaluate(metrics, self._thresholds)
        failures = [verdict.explanation for verdict in verdicts if verdict.passed is False]
        if error is not None:
            passed = False
            failures.insert(0, f"The research run did not finish ({error}).")
        return CaseOutcome(
            case=case,
            metrics=metrics,
            passed=passed,
            failures=tuple(failures),
            run_id=run_id,
            duration_seconds=round(duration, 3),
            error=error,
        )


def facts_of(state: ResearchState, *, duration_seconds: float, failed: bool = False) -> RunFacts:
    """What the scorers need, read out of a finished run's state.

    The citations are taken from the draft rather than from the `citations`
    table on purpose: the table's foreign keys already guarantee that a stored
    citation resolves, so scoring it would be measuring the database. What is
    worth measuring is what the *synthesizer produced*, before the validator
    filtered it - which is the number that says whether the model is citing
    honestly.
    """
    claims = list(state.get("claims") or ())
    evidence = list(state.get("evidence") or ())
    report = state.get("report")
    citations: list[tuple[uuid.UUID, uuid.UUID]] = []
    cited: list[uuid.UUID] = []
    if report is not None:
        by_id = {claim.id: claim for claim in claims}
        spans = {item.id: item for item in evidence}
        for section in report.sections:
            for claim_id in section.claim_ids:
                cited.append(claim_id)
                claim = by_id.get(claim_id)
                if claim is None:
                    # A marker pointing at a claim that does not exist is
                    # exactly the failure citation precision is for, so it is
                    # recorded as an unresolvable citation rather than skipped.
                    citations.append((claim_id, uuid.UUID(int=0)))
                    continue
                for eid in claim.evidence_ids:
                    span = spans.get(eid)
                    if span is not None:
                        citations.append((claim_id, span.source_id))
                        break

    cost = state.get("estimated_cost")
    return RunFacts(
        plan=state.get("research_plan"),
        subtasks=list(state.get("subtasks") or ()),
        completed=list(state.get("completed_tasks") or ()),
        sources=list(state.get("sources") or ()),
        claims=claims,
        evidence=evidence,
        citations=citations,
        cited_claim_ids=cited,
        contradictions=len(state.get("contradictions") or ()),
        has_report=report is not None,
        # None, never 0.0: a run with an unpriced call has no measured cost.
        cost_usd=None if cost is None or cost.uncosted_calls else round(cost.usd, 6),
        runtime_seconds=round(duration_seconds, 3),
        failed=failed,
    )


def git_sha(root: Path | None = None) -> str:
    """The commit under test, read from ``.git``. ``unknown`` when there is none.

    Recorded with every result so a metric that moved can be attributed to
    code rather than to data (``evaluations.git_sha``).

    Read rather than shelled out for. ``git rev-parse HEAD`` would be simpler
    and would also be the only ``subprocess`` call in the application - a
    capability the security suite keeps out of production code by keeping the
    exemption list at one file (the sandboxed document parser). Four lines of
    file reading is a smaller thing to own than a process-spawning primitive
    nobody else needs, and this walks the same three shapes git uses: a
    symbolic HEAD, a detached one, and a packed ref.

    ``unknown`` rather than a guess when this is not a checkout - a benchmark
    run from a container with no git history is still a real result, and a
    fabricated commit would make it a misleading one.
    """
    directory = _git_dir(root or REPO_ROOT)
    if directory is None:
        return "unknown"
    try:
        head = (directory / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    if not head.startswith("ref: "):
        # Detached: HEAD holds the sha itself.
        return head[:40] if _is_sha(head) else "unknown"

    ref = head.removeprefix("ref: ").strip()
    try:
        return (directory / ref).read_text(encoding="utf-8").strip()[:40]
    except OSError:
        return _packed(directory, ref)


def _git_dir(root: Path) -> Path | None:
    """``<root>/.git``, following the one-line pointer a worktree uses."""
    candidate = root / ".git"
    if candidate.is_dir():
        return candidate
    try:
        pointer = candidate.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not pointer.startswith("gitdir: "):
        return None
    target = Path(pointer.removeprefix("gitdir: ").strip())
    resolved = target if target.is_absolute() else (root / target)
    return resolved if resolved.is_dir() else None


def _packed(directory: Path, ref: str) -> str:
    """A ref that lives in ``packed-refs`` - the shape a fresh clone has."""
    try:
        lines = (directory / "packed-refs").read_text(encoding="utf-8").splitlines()
    except OSError:
        return "unknown"
    for line in lines:
        if line.startswith("#") or " " not in line:
            continue
        sha, name = line.split(" ", 1)
        if name.strip() == ref and _is_sha(sha):
            return sha[:40]
    return "unknown"


def _is_sha(value: str) -> bool:
    return len(value) == 40 and all(character in "0123456789abcdef" for character in value)


def describe(models: Sequence[str]) -> dict[str, str]:
    """The model routing in force, as the result records it."""
    return {f"model_{index}": key for index, key in enumerate(models)}
