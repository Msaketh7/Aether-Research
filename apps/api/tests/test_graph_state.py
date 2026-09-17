"""The graph's value types, its reducers, its clock, and the checkpoint serializer.

The serializer tests are the reason this file exists. LangGraph revives a stored
model only when its class is allowlisted, and a class that is not comes back as
a ``dict`` with nothing louder than a log line. So every type a checkpoint can
hold is round-tripped here through the serializer production uses, and the
failure mode is demonstrated rather than assumed.
"""

from __future__ import annotations

import types
from datetime import date, datetime, timedelta
from typing import Annotated, Union, get_args, get_origin, get_type_hints

import pytest
from langgraph.types import Send
from pydantic import ValidationError

from app.agents.checkpoint import allowed_msgpack_modules, build_serializer
from app.agents.schemas import (
    MAX_PLANNED_SUBTASKS,
    CitationCheck,
    ClaimItem,
    ContradictionItem,
    CostEstimate,
    Critique,
    EvidenceItem,
    GraphNode,
    GraphValue,
    MissingInfo,
    NodeError,
    Plan,
    ReportDraft,
    ReportSectionDraft,
    RunBudget,
    RunClock,
    RunParameters,
    SourceRef,
    Stop,
    StopReason,
    Subtask,
    SubtaskAssignment,
    TaskOutcome,
    TokenCount,
)
from app.agents.state import (
    ResearchState,
    initial_state,
    keep_stop,
    latest_clock,
    merge_by_id,
    merge_sources,
)
from app.core.config import Settings
from app.core.enums import (
    AgentName,
    ClaimStatus,
    ClaimType,
    EvidenceStance,
    ReportSectionKind,
    ResearchMode,
    TaskPriority,
)
from app.research.schemas import RunLimits
from tests.support.graph import START, brief, stable_id


def subtask(key: str = "market", *, iteration: int = 1) -> Subtask:
    return Subtask(
        key=key, question=f"About {key}?", priority=TaskPriority.HIGH, iteration=iteration
    )


def source(name: str, task_key: str = "market") -> SourceRef:
    return SourceRef(
        source_id=stable_id("source", name),
        task_key=task_key,
        title=f"Source {name}",
        url=f"https://example.com/{name}",
    )


def evidence(name: str = "e") -> EvidenceItem:
    return EvidenceItem(
        id=stable_id("evidence", name),
        task_key="market",
        iteration=1,
        source_id=stable_id("source", name),
        document_id=stable_id("document", name),
        claim_text="The market grew by a fifth.",
        span_start=0,
        span_end=27,
        stance=EvidenceStance.SUPPORTS,
    )


def claim(name: str) -> ClaimItem:
    return ClaimItem(
        id=stable_id("claim", name),
        normalized_key=f"market.{name}",
        text="The market grew by a fifth.",
        claim_type=ClaimType.QUANTITATIVE,
        confidence=0.6,
        evidence_ids=(evidence(name).id,),
    )


# --- values ---------------------------------------------------------------------


def test_a_plan_may_not_name_a_subtask_twice():
    with pytest.raises(ValidationError, match="same subtask twice"):
        Plan(research_goal="goal", iteration=1, subtasks=(subtask(), subtask()))


def test_a_plan_holds_only_its_own_rounds_subtasks():
    with pytest.raises(ValidationError, match="that plan's iteration"):
        Plan(research_goal="goal", iteration=2, subtasks=(subtask(iteration=1),))


def test_an_empty_plan_is_a_legitimate_answer():
    """A re-plan that finds nothing worth researching ends discovery; it is not an error."""
    assert Plan(research_goal="goal", iteration=2).subtasks == ()


def test_a_plan_cannot_propose_more_subtasks_than_the_ceiling():
    with pytest.raises(ValidationError):
        Plan(
            research_goal="goal",
            iteration=1,
            subtasks=tuple(subtask(f"t{index}") for index in range(MAX_PLANNED_SUBTASKS + 1)),
        )


def test_an_insufficient_critique_must_say_what_is_missing():
    """Otherwise another round would have nothing to research."""
    with pytest.raises(ValidationError, match="must say what is missing"):
        Critique(iteration=1, sufficient=False)


def test_every_checked_citation_is_valid_or_rejected():
    with pytest.raises(ValidationError, match="either valid or rejected"):
        CitationCheck(revision=0, checked=3, valid=1, rejected=1)


def test_rejected_citations_come_with_repair_instructions():
    with pytest.raises(ValidationError, match="repair instructions"):
        CitationCheck(revision=0, checked=1, valid=0, rejected=1)


def test_an_evidence_span_must_end_after_it_starts():
    with pytest.raises(ValidationError, match="end after it starts"):
        evidence().model_validate({**evidence().model_dump(), "span_start": 20, "span_end": 10})


def test_a_claim_without_evidence_is_refused():
    """A claim with nothing behind it could only ever be cited to nothing."""
    with pytest.raises(ValidationError):
        ClaimItem.model_validate({**claim("a").model_dump(), "evidence_ids": ()})


def test_a_claim_cannot_contradict_itself():
    with pytest.raises(ValidationError, match="cannot contradict itself"):
        ContradictionItem(
            id=stable_id("x"),
            normalized_key="market.growth",
            claim_a_id=claim("a").id,
            claim_b_id=claim("a").id,
            likely_reason="Different fiscal years.",
        )


def test_a_task_outcome_reports_only_its_own_sources():
    with pytest.raises(ValidationError, match="found for that task"):
        TaskOutcome(task_key="market", iteration=1, sources=(source("s", task_key="pricing"),))


def test_the_budget_is_exactly_the_limits_frozen_on_the_run():
    """Closed both ways: a ceiling added to RunLimits and not to the budget fails here."""
    for mode in ResearchMode:
        limits = RunLimits.for_mode(mode, Settings(app_env="test"))
        assert RunBudget.from_limits(limits).model_dump() == limits.model_dump()


def test_every_node_is_attributed_to_an_agent_role():
    """The role vocabulary is shared with the database and the frontend, so the
    contradiction checker is attributed to verification rather than widening it."""
    for node in GraphNode:
        assert isinstance(node.agent, AgentName)
    assert GraphNode.CONTRADICTION_CHECKER.agent is AgentName.VERIFIER
    assert {node for node in GraphNode if node.discovers} == {
        GraphNode.PLANNER,
        GraphNode.RESEARCHER,
    }


# --- reducers -------------------------------------------------------------------


def test_a_later_version_of_a_claim_replaces_the_earlier_one_in_place():
    """A verifier's re-scored claim must replace the candidate, or a report would
    cite one claim twice with two confidences."""
    first, second = claim("a"), claim("b")
    rescored = first.model_copy(update={"status": ClaimStatus.VERIFIED, "confidence": 0.9})
    assert merge_by_id([first, second], [rescored]) == [rescored, second]


def test_a_source_two_researchers_found_is_counted_once():
    found_by_a = source("s1", task_key="market")
    found_by_b = source("s1", task_key="pricing")
    other = source("s2", task_key="pricing")
    assert merge_sources([found_by_a], [found_by_b, other]) == [found_by_a, other]


def test_the_first_stop_is_kept_but_cancellation_overrides_it():
    """The first reason is the explanation, and its caveat is the one a report carries."""
    sources = Stop(reason=StopReason.SOURCES, caveat="sources caveat")
    runtime = Stop(reason=StopReason.RUNTIME, caveat="runtime caveat")
    cancelled = Stop(reason=StopReason.CANCELLED)
    assert keep_stop(sources, runtime) is sources
    assert keep_stop(None, runtime) is runtime
    assert keep_stop(sources, None) is sources
    assert keep_stop(sources, cancelled) is cancelled


def test_the_clock_keeps_its_furthest_reading_and_a_resume_re_anchors_it():
    early = RunClock(elapsed_seconds=10, as_of=START)
    later = RunClock(elapsed_seconds=25, as_of=START + timedelta(seconds=15))
    assert latest_clock(early, later) == later
    assert latest_clock(later, early) == later
    resumed = RunClock(elapsed_seconds=25, as_of=START + timedelta(hours=2))
    assert latest_clock(later, resumed) == resumed


# --- the clock ------------------------------------------------------------------


def test_downtime_before_a_resume_does_not_count_against_the_runtime_ceiling():
    """Three hours waiting for a replacement worker cost this run five seconds."""
    clock = RunClock(elapsed_seconds=40, as_of=START)
    recovered_at = START + timedelta(hours=3)
    assert clock.elapsed_at(recovered_at + timedelta(seconds=5), resumed_at=recovered_at) == 45


def test_time_since_the_last_node_counts_while_the_process_is_alive():
    clock = RunClock(elapsed_seconds=40, as_of=START)
    assert (
        clock.elapsed_at(START + timedelta(seconds=7), resumed_at=START - timedelta(minutes=1))
        == 47
    )


def test_a_run_clock_needs_a_timezone():
    with pytest.raises(ValidationError, match="timezone-aware"):
        RunClock(as_of=datetime(2026, 9, 13, 12, 0))


# --- checkpoint serialization -----------------------------------------------------


def full_state() -> dict[str, object]:
    """A state with every field populated by every type a checkpoint can hold."""
    state: dict[str, object] = dict(
        initial_state(
            brief().model_copy(
                update={
                    "domains": ("sec.gov",),
                    "date_range_start": date(2025, 1, 1),
                    "parent_research_id": stable_id("parent"),
                }
            ),
            now=START,
        )
    )
    found = source("s1")
    first, second = claim("a"), claim("b")
    state.update(
        iteration=1,
        research_plan=Plan(research_goal="Size the market.", iteration=1, subtasks=(subtask(),)),
        subtasks=[subtask()],
        sources=[found],
        completed_tasks=[TaskOutcome(task_key="market", iteration=1, sources=(found,))],
        failed_tasks=[
            NodeError(
                node=GraphNode.RESEARCHER,
                iteration=1,
                code="node_timeout",
                message="The step did not finish within its time limit.",
                task_key="pricing",
            )
        ],
        evidence=[evidence("a"), evidence("b")],
        claims=[first, second],
        contradictions=[
            ContradictionItem(
                id=stable_id("contradiction"),
                normalized_key="market.growth",
                claim_a_id=first.id,
                claim_b_id=second.id,
                likely_reason="Different fiscal years.",
            )
        ],
        critique=Critique(
            iteration=1, sufficient=False, missing=(MissingInfo(description="Pricing"),)
        ),
        report=ReportDraft(
            title="Market report",
            revision=1,
            coverage_caveat="Research stopped after collecting 1 sources.",
            sections=(
                ReportSectionDraft(
                    kind=ReportSectionKind.EXECUTIVE_SUMMARY,
                    heading="Summary",
                    content_md="The market grew.",
                    claim_ids=(first.id,),
                ),
            ),
        ),
        citation_check=CitationCheck(
            revision=1,
            checked=2,
            valid=1,
            rejected=1,
            repair_instructions="Cite the growth figure.",
        ),
        citation_repairs=1,
        token_usage=TokenCount(prompt_tokens=120, completion_tokens=45),
        estimated_cost=CostEstimate(usd=0.42, uncosted_calls=1),
        search_queries=3,
        clock=RunClock(elapsed_seconds=12.5, as_of=START),
        stop=Stop(reason=StopReason.SOURCES, caveat="Research stopped after collecting 1 sources."),
    )
    return state


def test_every_value_a_checkpoint_holds_comes_back_as_the_same_type():
    serde = build_serializer()
    state = full_state()

    restored = serde.loads_typed(serde.dumps_typed(state))

    assert restored == state
    for key, value in state.items():
        assert type(restored[key]) is type(value), key
        if isinstance(value, list):
            assert [type(item) for item in restored[key]] == [type(item) for item in value], key


def test_a_dispatched_researchers_work_order_survives_a_checkpoint():
    """A checkpoint written mid-round holds the pending sends, not just the state."""
    serde = build_serializer()
    assignment = SubtaskAssignment(
        research_id=stable_id("run"),
        user_id=stable_id("user"),
        query="How do providers price inference?",
        mode=ResearchMode.DEEP,
        subtask=subtask(),
        query_allowance=3,
        source_allowance=5,
        time_allowance_seconds=120.0,
        domains=("sec.gov",),
    )

    restored = serde.loads_typed(serde.dumps_typed(Send("researcher", assignment)))

    assert isinstance(restored, Send)
    assert type(restored.arg) is SubtaskAssignment
    assert restored.arg == assignment


class _Outsider(GraphValue):
    note: str


def test_a_type_off_the_allowlist_comes_back_as_a_dict():
    """The failure the derived allowlist exists to prevent, shown rather than described:
    no error, just a dict where a model was stored."""
    serde = build_serializer()
    restored = serde.loads_typed(serde.dumps_typed({"value": _Outsider(note="hello")}))
    assert restored["value"] == {"note": "hello"}


def test_the_allowlist_holds_only_this_applications_types():
    """Anything else would be a class a tampered checkpoint could name."""
    assert all(module.startswith("app.") for module, _ in allowed_msgpack_modules())


def test_the_allowlist_reaches_nested_models_and_enums():
    allowed = set(allowed_msgpack_modules())
    for cls in (
        Plan,
        Subtask,
        SubtaskAssignment,
        RunBudget,
        RunClock,
        RunParameters,
        Stop,
        NodeError,
        MissingInfo,
        ReportSectionDraft,
        GraphNode,
        StopReason,
        ResearchMode,
        TaskPriority,
        EvidenceStance,
        ClaimStatus,
        ReportSectionKind,
    ):
        assert (cls.__module__, cls.__name__) in allowed, cls.__name__


def _top_level_types(annotation: object) -> list[object]:
    origin = get_origin(annotation)
    if origin is Annotated:
        return _top_level_types(get_args(annotation)[0])
    if origin in (Union, types.UnionType):
        return [leaf for argument in get_args(annotation) for leaf in _top_level_types(argument)]
    return [origin or annotation]


def test_no_top_level_state_field_is_a_subclass_of_a_json_primitive():
    """Found on Postgres, where an enum at the top of the state came back as a string.

    The Postgres checkpointer stores a top-level value that is an instance of str,
    int, float or bool inline in a JSON column. A StrEnum is such an instance, so
    it is written as its text and read back as a plain str - and
    `stop is StopReason.CANCELLED` quietly stops matching. Values like that
    belong inside a model, which is stored and validated as the model.
    """
    primitives = (str, int, float, bool)
    for name, annotation in get_type_hints(ResearchState, include_extras=True).items():
        for leaf in _top_level_types(annotation):
            if not isinstance(leaf, type) or leaf in primitives:
                continue
            assert not issubclass(leaf, primitives), (
                f"{name} is a {leaf.__name__}, which the Postgres checkpointer would read back "
                "as a plain primitive. Put it inside a model."
            )
