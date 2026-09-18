"""The evaluation suite: scoring, gating, and refusing to invent a number.

The scorers are pure functions and are tested as such. What is checked over
and over is the same property, because it is the one the whole document rests
on: a metric that could not be measured is ``None``, and nothing downstream -
the aggregate, the gate, the report, the API - is allowed to turn that into a
zero.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from httpx import AsyncClient

from app.agents.schemas import (
    ClaimItem,
    EvidenceItem,
    Plan,
    ReportDraft,
    ReportSectionDraft,
    SourceRef,
    Subtask,
    TaskOutcome,
)
from app.core.enums import (
    ClaimType,
    EvaluationKind,
    EvidenceStance,
    ReportSectionKind,
    SourceType,
    TaskPriority,
)
from app.db.repositories.evaluations import SqlAlchemyEvaluationStore
from app.evaluations import metrics as scoring
from app.evaluations.dataset import (
    DATASET_DIR,
    Dataset,
    EvaluationCase,
    ExpectedClaim,
    available,
    load_dataset,
)
from app.evaluations.report import render
from app.evaluations.runner import BenchmarkResult, BenchmarkRunner, CaseOutcome, facts_of, git_sha
from app.evaluations.schemas import metric_values
from app.evaluations.thresholds import Thresholds, evaluate
from app.research.schemas import RunLimits
from tests.conftest import API
from tests.support.graph import Script, ScriptedNodes, make_runner
from tests.support.worker import worker_settings


@pytest.fixture
def worker_config(settings):
    return worker_settings(settings)


def case(**overrides: object) -> EvaluationCase:
    fields: dict[str, object] = {
        "id": "pricing-comparison",
        "question": "Compare the published pricing of the major inference providers.",
        "expected_topics": ("pricing", "throughput"),
        "expected_source_types": (SourceType.WEB,),
        "expected_claims": (ExpectedClaim(key="pricing.per-token"),),
    }
    fields.update(overrides)
    return EvaluationCase.model_validate(fields)


def subtask(key: str, question: str) -> Subtask:
    return Subtask(key=key, question=question, priority=TaskPriority.MEDIUM, iteration=1)


def source(index: int = 0, kind: SourceType = SourceType.WEB) -> SourceRef:
    return SourceRef(
        source_id=uuid.UUID(int=100 + index),
        task_key="task-1",
        title=f"Source {index}",
        url=f"https://example.com/{index}",
        source_type=kind,
    )


def evidence(index: int = 0, source_index: int = 0) -> EvidenceItem:
    text = f"A finding numbered {index}"
    return EvidenceItem(
        id=uuid.UUID(int=200 + index),
        task_key="task-1",
        iteration=1,
        source_id=uuid.UUID(int=100 + source_index),
        document_id=uuid.UUID(int=300 + source_index),
        claim_text=text,
        span_start=0,
        span_end=len(text),
        stance=EvidenceStance.SUPPORTS,
    )


def claim(index: int = 0, *, key: str = "pricing.per-token", evidence_ids: tuple = ()) -> ClaimItem:
    return ClaimItem(
        id=uuid.UUID(int=400 + index),
        normalized_key=key,
        text=f"Claim number {index}",
        claim_type=ClaimType.QUANTITATIVE,
        confidence=0.8,
        evidence_ids=evidence_ids or (uuid.UUID(int=200 + index),),
    )


def facts(**overrides: object) -> scoring.RunFacts:
    fields: dict[str, object] = {
        "plan": Plan(research_goal="Compare pricing", iteration=1, subtasks=()),
        "subtasks": [subtask("task-1", "What is the pricing per token?")],
        "completed": [TaskOutcome(task_key="task-1", iteration=1, sources=(source(),))],
        "sources": [source()],
        "claims": [claim(0)],
        "evidence": [evidence(0)],
        "citations": [(uuid.UUID(int=400), uuid.UUID(int=100))],
        "cited_claim_ids": [uuid.UUID(int=400)],
    }
    fields.update(overrides)
    return scoring.RunFacts(**fields)  # type: ignore[arg-type]


# --- the dataset -----------------------------------------------------------


def test_the_shipped_dataset_loads_and_is_versioned():
    """A case with a misspelled field would otherwise be scored silently,
    producing a metric that looks measured and is not."""
    paths = available()

    assert paths, f"no dataset in {DATASET_DIR}"
    for path in paths:
        dataset = load_dataset(path)
        assert dataset.version
        assert dataset.cases


def test_the_dataset_covers_the_four_case_classes():
    """docs/evaluation.md names them, and the one that matters most is the
    question with no good sources - whose correct answer is an honest
    low-coverage report rather than a confident one."""
    kinds = {case.checks for path in available() for case in load_dataset(path).cases}

    assert kinds == {"factual", "comparison", "contradiction", "no-good-sources"}


def test_a_case_that_is_not_valid_is_refused():
    with pytest.raises(ValueError, match=r"(?i)validation"):
        Dataset.model_validate({"version": "v1", "cases": [{"id": "X bad id", "question": "hi"}]})


# --- scoring ---------------------------------------------------------------


def test_topic_coverage_counts_what_the_plan_actually_mentioned():
    covered = scoring.topic_coverage(
        case(),
        facts(subtasks=[subtask("a", "pricing per token"), subtask("b", "throughput limits")]),
    )
    half = scoring.topic_coverage(case(), facts(subtasks=[subtask("a", "pricing per token")]))

    assert covered == 1.0
    assert half == 0.5


def test_a_case_that_names_no_topics_has_no_coverage_to_report():
    """``None``, not 1.0: nothing was asked for, so nothing was measured."""
    assert scoring.topic_coverage(case(expected_topics=()), facts()) is None


def test_claim_recall_matches_on_the_normalised_key():
    """Two correct runs word a fact differently and key it the same - which
    is what the claim normalizer exists for."""
    found = scoring.claim_recall(case(), facts(claims=[claim(0, key="pricing.per-token.input")]))
    missed = scoring.claim_recall(case(), facts(claims=[claim(0, key="funding.series-c")]))

    assert (found, missed) == (1.0, 0.0)


def test_citation_precision_walks_the_whole_chain():
    """A citation whose source no evidence for that claim came from is wrong,
    which is the failure a reader finds by clicking it."""
    good = scoring.citation_precision(facts())
    wrong_source = scoring.citation_precision(
        facts(citations=[(uuid.UUID(int=400), uuid.UUID(int=999))])
    )
    missing_claim = scoring.citation_precision(
        facts(citations=[(uuid.UUID(int=555), uuid.UUID(int=100))])
    )

    assert good == 1.0
    assert wrong_source == 0.0
    assert missing_claim == 0.0


def test_a_run_that_cited_nothing_has_no_precision_to_report():
    assert scoring.citation_precision(facts(citations=[])) is None
    assert scoring.citation_recall(facts(cited_claim_ids=[])) is None


def test_groundedness_is_the_structural_half_only():
    """Whether a span supports a claim is a judgement; whether one exists is
    arithmetic, and a system failing this is broken rather than imprecise."""
    grounded = scoring.groundedness(facts())
    ungrounded = scoring.groundedness(facts(claims=[claim(0, evidence_ids=(uuid.UUID(int=777),))]))

    assert (grounded, ungrounded) == (1.0, 0.0)


def test_a_source_nothing_cited_is_counted_as_waste():
    rate = scoring.unnecessary_source_rate(facts(sources=[source(0), source(1)]))

    assert rate == 0.5


def test_the_aggregate_skips_what_nobody_measured():
    """A metric no case measured aggregates to ``None``. Averaging it in as
    zero would make an unlabelled dataset look like a broken system."""
    summary = scoring.aggregate(
        [
            {**dict.fromkeys(scoring.METRIC_KEYS), "claim_recall": 1.0},
            {**dict.fromkeys(scoring.METRIC_KEYS), "claim_recall": 0.5},
        ]
    )

    assert summary["claim_recall"] == 0.75
    assert summary["citation_precision"] is None


# --- the gate --------------------------------------------------------------


def test_a_metric_that_was_not_measured_cannot_fail_a_build():
    """A gate that fails on absence teaches everyone to ignore the gate."""
    passed, verdicts = evaluate({"citation_precision": None}, Thresholds(citation_precision=0.9))

    assert passed is True
    assert verdicts[0].passed is None
    assert "not measured" in verdicts[0].explanation


def test_a_floor_and_a_ceiling_are_compared_in_opposite_directions():
    """Guessing the direction from the name is how a gate ends up enforcing
    the opposite of what someone meant."""
    floors, _ = evaluate({"citation_precision": 0.8}, Thresholds(citation_precision=0.9))
    ceilings, _ = evaluate(
        {"unnecessary_source_rate": 0.8}, Thresholds(unnecessary_source_rate=0.9)
    )

    assert floors is False
    assert ceilings is True


def test_nothing_is_gated_until_there_is_a_baseline():
    """A threshold written before a measurement is an aspiration presented as
    a requirement (docs/evaluation.md §5)."""
    assert Thresholds().gated() == {}


# --- running a dataset -----------------------------------------------------


async def test_a_benchmark_runs_every_case_and_scores_it():
    """Through the real graph runner, over scripted agents: a benchmark that
    exercised a special evaluation path would measure that path."""
    runner, _, _ = make_runner(ScriptedNodes(Script()))
    store = _Collecting()
    dataset = Dataset(version="test-v1", cases=(case(), case(id="second-case")))

    result = await BenchmarkRunner(graph=runner, store=store, limits=_limits()).run(
        dataset, user_id=uuid.uuid4()
    )

    assert len(result.cases) == 2
    assert store.recorded is result
    assert result.metrics["groundedness"] is not None
    assert result.git_sha


async def test_a_case_that_fails_is_a_result_not_a_crash():
    """A benchmark that stops at the first failure tells you about one case."""
    runner, _, _ = make_runner(ScriptedNodes(Script(fail_on={"planner": {1}})))
    dataset = Dataset(version="test-v1", cases=(case(), case(id="second-case")))

    result = await BenchmarkRunner(graph=runner, store=_Collecting(), limits=_limits()).run(
        dataset, user_id=uuid.uuid4()
    )

    assert len(result.cases) == 2
    assert result.passed is False
    assert any(
        "did not finish" in failure for outcome in result.cases for failure in outcome.failures
    )


async def test_an_unpriced_run_reports_no_cost():
    """Not 0.0. A run with an uncosted call has no measured cost."""
    runner, _, _ = make_runner(ScriptedNodes(Script(uncosted=True)))
    dataset = Dataset(version="test-v1", cases=(case(),))

    result = await BenchmarkRunner(graph=runner, store=_Collecting(), limits=_limits()).run(
        dataset, user_id=uuid.uuid4()
    )

    assert result.cases[0].metrics["cost_usd"] is None


def test_facts_record_a_citation_that_points_at_no_claim():
    """Exactly the failure citation precision exists to catch, so it is
    recorded as unresolvable rather than quietly skipped."""
    draft = ReportDraft(
        title="Report",
        revision=0,
        sections=(
            ReportSectionDraft(
                kind=ReportSectionKind.EXECUTIVE_SUMMARY,
                heading="Summary",
                content_md="Something was found.",
                claim_ids=(uuid.UUID(int=999),),
            ),
        ),
    )

    read = facts_of({"report": draft, "claims": [], "evidence": []}, duration_seconds=1.0)

    assert read.citations == [(uuid.UUID(int=999), uuid.UUID(int=0))]
    assert scoring.citation_precision(read) == 0.0


def test_the_commit_is_recorded_or_reported_as_unknown():
    """A benchmark run from a container with no git history is still a real
    result; a guessed commit would not be."""
    assert git_sha() and len(git_sha()) <= 40


# --- the report ------------------------------------------------------------


def test_the_report_says_not_measured_rather_than_zero():
    result = _result({"citation_precision": None, "groundedness": 1.0})

    text = render(result)

    assert "not measured" in text
    assert "Groundedness" in text
    assert "No metric is gated" in text


def test_the_report_names_the_dataset_and_the_commit():
    """A number without those is not comparable to any other number."""
    text = render(_result({"groundedness": 1.0}))

    assert "test-v1" in text
    assert "commit" in text


# --- persistence and the endpoint ------------------------------------------


async def test_a_result_is_stored_one_row_per_case(database, worker_config):
    store = SqlAlchemyEvaluationStore(database)

    await store.record(_result({"groundedness": 1.0, "citation_precision": None}))

    response = await store.latest()
    assert response.latest is not None
    assert response.latest.dataset_version == "test-v1"
    assert response.latest.case_count == 1
    assert len(response.cases) == 1
    measured = {metric.key: metric.value for metric in response.cases[0].metrics}
    assert measured["groundedness"] == 1.0
    # Absent from the row, so absent from the response - never zero.
    assert "citation_precision" not in measured


async def test_the_endpoint_says_nothing_has_run(client: AsyncClient):
    response = await client.get(f"{API}/evaluations")

    assert response.status_code == 200
    body = response.json()
    assert body["latest"] is None
    assert body["history"] == []


async def test_the_endpoint_serves_a_stored_benchmark(client: AsyncClient, database, worker_config):
    await SqlAlchemyEvaluationStore(database).record(_result({"groundedness": 0.75}))

    body = (await client.get(f"{API}/evaluations")).json()

    assert body["latest"]["dataset_version"] == "test-v1"
    assert body["latest"]["passed"] is True
    grounded = next(m for m in body["latest"]["metrics"] if m["key"] == "groundedness")
    assert grounded["value"] == 0.75
    assert grounded["unit"] == "ratio"


def test_a_metric_value_carries_no_delta_against_nothing():
    """ "Improved from nothing" is not a change, and rendering it as one would
    invent a trend."""
    values = metric_values({"groundedness": 0.9}, previous={"groundedness": None})

    assert values[0].delta is None


def _limits() -> RunLimits:
    return RunLimits(
        max_iterations=2,
        max_sources=20,
        max_search_queries=10,
        max_runtime_seconds=120,
        max_cost_usd=1.0,
    )


def _result(metrics: dict[str, float | None]) -> BenchmarkResult:
    now = dt.datetime.now(dt.UTC)
    complete = {key: metrics.get(key) for key in scoring.METRIC_KEYS}
    return BenchmarkResult(
        id=uuid.uuid4(),
        dataset_version="test-v1",
        git_sha="0" * 40,
        kind=EvaluationKind.FULL,
        model_config_used={"anthropic:sonnet": "claude-sonnet"},
        started_at=now - dt.timedelta(seconds=3),
        completed_at=now,
        thresholds=Thresholds(),
        cases=(
            CaseOutcome(
                case=case(),
                metrics=complete,
                passed=True,
                failures=(),
                run_id=uuid.uuid4(),
                duration_seconds=3.0,
            ),
        ),
    )


class _Collecting:
    """A store that keeps what it was given."""

    def __init__(self) -> None:
        self.recorded: BenchmarkResult | None = None

    async def record(self, result: BenchmarkResult) -> None:
        self.recorded = result
