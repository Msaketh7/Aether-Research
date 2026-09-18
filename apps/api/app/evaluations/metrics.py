"""Scoring one finished run against one case (Phase 18, docs/evaluation.md).

Pure functions over what a run actually produced. Nothing here calls a model,
opens a connection or knows about a table: a scorer that needs the world to be
in a particular state is a scorer nobody can debug when a number moves.

Two rules run through all of it, and they are the same rule twice.

**Unmeasurable is ``None``, never zero.** A case with no expected claims
cannot have a claim-recall; a run with no citations cannot have a citation
precision. Returning ``0.0`` for either would make an incompletely labelled
dataset look like a broken system, and would make every average a lie about
what it averaged. ``app.retrieval.metrics`` already established this for
retrieval, and ``Mean`` there already counts how many samples it skipped.

**Structural before semantic.** Whether a citation resolves - claim to
evidence to document to source - is a fact about the data and is checked here.
Whether the evidence actually *supports* the claim is a judgement, needs a
model, and lives in ``app.evaluations.judge``. A structural failure is a bug;
conflating the two hides bugs inside model-quality noise.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from app.agents.schemas import ClaimItem, EvidenceItem, Plan, SourceRef, Subtask, TaskOutcome
from app.evaluations.dataset import EvaluationCase


@dataclass(frozen=True, slots=True)
class RunFacts:
    """Everything the scorers read about one finished run.

    Assembled by the runner from graph state and rows, so the scorers stay
    pure and a test can build one by hand in four lines.
    """

    plan: Plan | None
    subtasks: Sequence[Subtask]
    completed: Sequence[TaskOutcome]
    sources: Sequence[SourceRef]
    claims: Sequence[ClaimItem]
    evidence: Sequence[EvidenceItem]
    #: Citations the report emitted, as ``(claim_id, source_id)``.
    citations: Sequence[tuple[UUID, UUID]]
    #: Claim ids the report's prose actually needed a citation for.
    cited_claim_ids: Sequence[UUID]
    contradictions: int = 0
    has_report: bool = False
    #: ``None`` when the run could not be costed - never 0.0.
    cost_usd: float | None = None
    runtime_seconds: float | None = None
    failed: bool = False


# --- planning -------------------------------------------------------------


def topic_coverage(case: EvaluationCase, facts: RunFacts) -> float | None:
    """Share of the case's expected topics some subtask mentions.

    Substring matching on lower-cased text, which is crude and deliberately
    so: the alternative is an embedding similarity threshold, which is a
    second model's opinion presented as a measurement. A topic the planner
    phrased differently scores as a miss, and that is a dataset to fix rather
    than a metric to soften.

    ``None`` when the case names no topics: there is nothing to cover.
    """
    if not case.expected_topics:
        return None
    haystack = " ".join(
        part.lower() for subtask in facts.subtasks for part in (subtask.question, subtask.rationale)
    )
    if facts.plan is not None:
        haystack += " " + facts.plan.research_goal.lower()
    found = sum(1 for topic in case.expected_topics if topic.lower() in haystack)
    return round(found / len(case.expected_topics), 4)


def task_completion(facts: RunFacts) -> float | None:
    """Share of dispatched subtasks that came back with an outcome.

    Measured against what was *dispatched*, not against what was planned: the
    governor deliberately holds subtasks back when a budget is tight, and
    counting those as failures would turn cost control into a quality
    regression.
    """
    dispatched = {outcome.task_key for outcome in facts.completed}
    attempted = dispatched | {subtask.key for subtask in facts.subtasks}
    if not attempted:
        return None
    produced = sum(1 for outcome in facts.completed if outcome.sources)
    return round(produced / len(attempted), 4)


# --- sourcing -------------------------------------------------------------


def source_type_coverage(case: EvaluationCase, facts: RunFacts) -> float | None:
    """Share of the source types the case expected that the run actually read."""
    if not case.expected_source_types:
        return None
    reached = {source.source_type for source in facts.sources}
    found = sum(1 for kind in case.expected_source_types if kind in reached)
    return round(found / len(case.expected_source_types), 4)


def unnecessary_source_rate(facts: RunFacts) -> float | None:
    """Share of collected sources that no evidence span points into.

    The agent-efficiency metric the brief calls "unnecessary tool calls",
    measured where the waste is visible: a page that was fetched, parsed,
    chunked and embedded, and then cited by nothing. Some of that is the cost
    of searching, so this is a number to watch rather than a gate.
    """
    if not facts.sources:
        return None
    used = {item.source_id for item in facts.evidence}
    return round(
        sum(1 for source in facts.sources if source.source_id not in used) / len(facts.sources), 4
    )


# --- claims ---------------------------------------------------------------


def claim_recall(case: EvaluationCase, facts: RunFacts) -> float | None:
    """Share of the case's required claims the run produced.

    Matched on the normalised key, which is what the claim normalizer exists
    to make stable: two correct runs word a fact differently and key it the
    same.
    """
    required = [claim for claim in case.expected_claims if claim.must_be_present]
    if not required:
        return None
    keys = " || ".join(claim.normalized_key.lower() for claim in facts.claims)
    found = sum(1 for expected in required if expected.key.lower() in keys)
    return round(found / len(required), 4)


def evidence_per_claim(facts: RunFacts) -> float | None:
    """Mean spans behind a claim. A claim with one source is corroborated by nothing."""
    if not facts.claims:
        return None
    total = sum(len(claim.evidence_ids) for claim in facts.claims)
    return round(total / len(facts.claims), 4)


# --- citations ------------------------------------------------------------


def citation_precision(facts: RunFacts) -> float | None:
    """Share of emitted citations that resolve, structurally.

    The chain is `citation -> claim -> evidence -> source`, and every link is
    checked against the run's own data rather than assumed. A citation whose
    claim does not exist, or whose source no evidence for that claim came
    from, is counted as wrong - which is the failure a reader would find by
    clicking it.
    """
    if not facts.citations:
        return None
    claims = {claim.id: claim for claim in facts.claims}
    evidence = {item.id: item for item in facts.evidence}
    resolved = 0
    for claim_id, source_id in facts.citations:
        claim = claims.get(claim_id)
        if claim is None:
            continue
        sources = {evidence[eid].source_id for eid in claim.evidence_ids if eid in evidence}
        resolved += 1 if source_id in sources else 0
    return round(resolved / len(facts.citations), 4)


def citation_recall(facts: RunFacts) -> float | None:
    """Share of the claims that needed a citation which carry one."""
    if not facts.cited_claim_ids:
        return None
    cited = {claim_id for claim_id, _ in facts.citations}
    found = sum(1 for claim_id in facts.cited_claim_ids if claim_id in cited)
    return round(found / len(facts.cited_claim_ids), 4)


def groundedness(facts: RunFacts) -> float | None:
    """Share of claims with at least one evidence span behind them.

    The structural half of groundedness. Whether the span *supports* the claim
    is the judge's question; whether one exists at all is arithmetic, and a
    system that fails this is broken rather than imprecise.
    """
    if not facts.claims:
        return None
    known = {item.id for item in facts.evidence}
    grounded = sum(1 for claim in facts.claims if any(eid in known for eid in claim.evidence_ids))
    return round(grounded / len(facts.claims), 4)


# --- assembling a case's score --------------------------------------------

#: The metric keys a case result may carry. Closed, because the report and the
#: thresholds are written against it and a typo would otherwise be a metric
#: that silently never gates anything.
METRIC_KEYS = (
    "topic_coverage",
    "task_completion",
    "source_type_coverage",
    "unnecessary_source_rate",
    "claim_recall",
    "evidence_per_claim",
    "citation_precision",
    "citation_recall",
    "groundedness",
    "contradictions_found",
    "cost_usd",
    "runtime_seconds",
)


def score_case(case: EvaluationCase, facts: RunFacts) -> dict[str, float | None]:
    """Every structural metric for one case, measured or ``None``."""
    return {
        "topic_coverage": topic_coverage(case, facts),
        "task_completion": task_completion(facts),
        "source_type_coverage": source_type_coverage(case, facts),
        "unnecessary_source_rate": unnecessary_source_rate(facts),
        "claim_recall": claim_recall(case, facts),
        "evidence_per_claim": evidence_per_claim(facts),
        "citation_precision": citation_precision(facts),
        "citation_recall": citation_recall(facts),
        "groundedness": groundedness(facts),
        "contradictions_found": float(facts.contradictions),
        "cost_usd": facts.cost_usd,
        "runtime_seconds": facts.runtime_seconds,
    }


def aggregate(results: Iterable[Mapping[str, float | None]]) -> dict[str, float | None]:
    """Mean of each metric across cases, skipping the ones not measured.

    A metric no case measured aggregates to ``None`` rather than to zero, so
    an aggregate can say "this was never measured" - which is what a report of
    a partially labelled dataset has to be able to say.
    """
    rows = list(results)
    summary: dict[str, float | None] = {}
    for key in METRIC_KEYS:
        values = [row[key] for row in rows if row.get(key) is not None]
        summary[key] = (
            round(sum(v for v in values if v is not None) / len(values), 4) if values else None
        )
    return summary
