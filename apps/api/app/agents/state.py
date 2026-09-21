"""The research graph's state (Phase 9, TDD 4.3, ADR 0014).

One TypedDict, checkpointed after every node. It holds the fields the build plan
names - ``research_id``, ``query``, ``research_plan``, ``subtasks``, ``sources``,
``claims``, ``evidence``, ``contradictions``, ``completed_tasks``,
``failed_tasks``, ``iteration``, ``token_usage``, ``estimated_cost``,
``critique``, ``report`` - and what loop control needs beside them.

A reducer decides what happens when more than one node writes a field in the
same step, which is exactly what parallel researchers do. A field without one
may be written by a single node per step, and LangGraph refuses anything else -
so the annotations below are the concurrency contract, not decoration:

* collections that researchers write in parallel merge - by identity where the
  same thing can be reported twice, by appending where it cannot;
* counters add;
* the run clock keeps its furthest-advanced reading;
* the first stop sticks, except that cancellation overrides a limit.

One rule is about storage rather than concurrency. LangGraph's Postgres
checkpointer writes a top-level value that is an instance of ``str``, ``int``,
``float`` or ``bool`` inline as JSON, so a subclass of one - a ``StrEnum`` - is
read back as the plain primitive. No top-level field may be such a subclass;
enums live inside models (``RunParameters``, ``Stop``), and a test enforces it.
"""

from __future__ import annotations

import operator
from datetime import date, datetime
from typing import Annotated, Protocol, TypedDict
from uuid import UUID

from pydantic import Field

from app.agents.budget import Consumption
from app.agents.schemas import (
    AnswerDraft,
    CitationCheck,
    ClaimItem,
    ContradictionItem,
    CostEstimate,
    Critique,
    EvidenceItem,
    GraphValue,
    NodeError,
    Plan,
    ReportDraft,
    RunBudget,
    RunClock,
    RunParameters,
    SourceRef,
    Stop,
    StopReason,
    Subtask,
    TaskOutcome,
    TokenCount,
)
from app.core.enums import ResearchMode
from app.research.schemas import (
    MAX_DEPTH,
    MAX_DOMAINS,
    MAX_QUESTION_LENGTH,
    MIN_DEPTH,
    ResearchRun,
)

# --- reducers -------------------------------------------------------------------


class _Identified(Protocol):
    @property
    def id(self) -> UUID: ...


def merge_by_id[T: _Identified](left: list[T] | None, right: list[T] | None) -> list[T]:
    """Later versions replace earlier ones by id, keeping the first-seen position.

    A verifier returns the claims it re-scored. They must replace the candidates
    rather than sit beside them, or a report would cite one claim twice with two
    confidences.
    """
    merged: dict[UUID, T] = {item.id: item for item in left or ()}
    for item in right or ():
        merged[item.id] = item
    return list(merged.values())


def merge_sources(left: list[SourceRef] | None, right: list[SourceRef] | None) -> list[SourceRef]:
    """One entry per source; the first researcher to report it keeps it.

    Two researchers can find the same page. Counting it twice would spend the
    source ceiling twice and inflate corroboration - the failure deduplication
    exists to prevent.
    """
    merged: dict[UUID, SourceRef] = {}
    for item in (*(left or ()), *(right or ())):
        merged.setdefault(item.source_id, item)
    return list(merged.values())


def add_tokens(left: TokenCount | None, right: TokenCount | None) -> TokenCount:
    return (left or TokenCount()) + (right or TokenCount())


def add_cost(left: CostEstimate | None, right: CostEstimate | None) -> CostEstimate:
    return (left or CostEstimate()) + (right or CostEstimate())


def latest_clock(left: RunClock | None, right: RunClock | None) -> RunClock:
    """The furthest-advanced reading; on a tie, the later anchor.

    A tie is what a resume produces: the same elapsed time, re-anchored at the
    moment the new process picked the run up.
    """
    if left is None:
        return right or RunClock()
    if right is None:
        return left
    if right.elapsed_seconds != left.elapsed_seconds:
        return right if right.elapsed_seconds > left.elapsed_seconds else left
    return right if right.as_of >= left.as_of else left


def keep_stop(left: Stop | None, right: Stop | None) -> Stop | None:
    """The first stop sticks - its reason is the explanation, and its caveat is
    the one the report carries - except that a cancellation overrides."""
    if right is not None and right.reason is StopReason.CANCELLED:
        return right
    return left if left is not None else right


# --- state ----------------------------------------------------------------------


class ResearchState(TypedDict, total=False):
    # --- what was asked: written once, when the run starts --------------------
    research_id: UUID
    user_id: UUID
    query: str
    parameters: RunParameters
    budget: RunBudget

    # --- the work ----------------------------------------------------------------
    #: Planning rounds started. 0 until the first plan exists.
    iteration: int
    #: The latest round's plan. Every round's subtasks remain in ``subtasks``.
    research_plan: Plan | None
    subtasks: Annotated[list[Subtask], operator.add]
    sources: Annotated[list[SourceRef], merge_sources]
    completed_tasks: Annotated[list[TaskOutcome], operator.add]
    failed_tasks: Annotated[list[NodeError], operator.add]
    evidence: Annotated[list[EvidenceItem], merge_by_id]
    claims: Annotated[list[ClaimItem], merge_by_id]
    contradictions: Annotated[list[ContradictionItem], merge_by_id]
    critique: Critique | None
    #: The direct answer, written before the report and streamed as it was
    #: written. Present from the moment the answerer finishes, which is what
    #: stops a resumed run answering a second time.
    answer: AnswerDraft | None
    report: ReportDraft | None
    citation_check: CitationCheck | None
    #: Repairs the validator has been granted, at most ``MAX_CITATION_REPAIRS``.
    citation_repairs: int

    # --- accounting and control -----------------------------------------------
    token_usage: Annotated[TokenCount, add_tokens]
    estimated_cost: Annotated[CostEstimate, add_cost]
    search_queries: Annotated[int, operator.add]
    clock: Annotated[RunClock, latest_clock]
    stop: Annotated[Stop | None, keep_stop]
    errors: Annotated[list[NodeError], operator.add]


class RunBrief(GraphValue):
    """What a run was asked, as the graph starts it. Spread into the first state."""

    research_id: UUID
    user_id: UUID
    query: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    mode: ResearchMode
    depth: int = Field(ge=MIN_DEPTH, le=MAX_DEPTH)
    domains: tuple[str, ...] = Field(default=(), max_length=MAX_DOMAINS)
    date_range_start: date | None = None
    date_range_end: date | None = None
    parent_research_id: UUID | None = None
    #: Set by whoever picks the run up, from the run's uploads (Phase 13). Not
    #: derivable from ``ResearchRun``, which counts sources rather than
    #: attachments, so it is passed in rather than guessed at.
    has_attached_documents: bool = False
    budget: RunBudget

    @classmethod
    def from_run(cls, run: ResearchRun, *, has_attached_documents: bool = False) -> RunBrief:
        return cls(
            research_id=run.id,
            user_id=run.user_id,
            query=run.question,
            mode=run.mode,
            depth=run.depth,
            domains=tuple(run.domains),
            date_range_start=run.date_range_start,
            date_range_end=run.date_range_end,
            parent_research_id=run.parent_run_id,
            has_attached_documents=has_attached_documents,
            budget=RunBudget.from_limits(run.limits),
        )

    @property
    def parameters(self) -> RunParameters:
        return RunParameters(
            mode=self.mode,
            depth=self.depth,
            domains=self.domains,
            date_range_start=self.date_range_start,
            date_range_end=self.date_range_end,
            parent_research_id=self.parent_research_id,
            has_attached_documents=self.has_attached_documents,
        )


def initial_state(brief: RunBrief, *, now: datetime) -> ResearchState:
    """Every key present from the start, so no node has to guess at a default."""
    return ResearchState(
        research_id=brief.research_id,
        user_id=brief.user_id,
        query=brief.query,
        parameters=brief.parameters,
        budget=brief.budget,
        iteration=0,
        research_plan=None,
        subtasks=[],
        sources=[],
        completed_tasks=[],
        failed_tasks=[],
        evidence=[],
        claims=[],
        contradictions=[],
        critique=None,
        answer=None,
        report=None,
        citation_check=None,
        citation_repairs=0,
        token_usage=TokenCount(),
        estimated_cost=CostEstimate(),
        search_queries=0,
        clock=RunClock(as_of=now),
        stop=None,
        errors=[],
    )


def stop_reason(state: ResearchState) -> StopReason | None:
    stop = state.get("stop")
    return None if stop is None else stop.reason


def consumption(
    state: ResearchState,
    *,
    now: datetime | None = None,
    resumed_at: datetime | None = None,
) -> Consumption:
    """What the run has used. Without ``now``, runtime is as of the last node."""
    clock = state.get("clock") or RunClock()
    if now is None or resumed_at is None:
        runtime = clock.elapsed_seconds
    else:
        runtime = clock.elapsed_at(now, resumed_at=resumed_at)
    return Consumption(
        iterations=state.get("iteration", 0),
        sources=len(state.get("sources") or ()),
        search_queries=state.get("search_queries", 0),
        runtime_seconds=runtime,
        cost=state.get("estimated_cost") or CostEstimate(),
    )
