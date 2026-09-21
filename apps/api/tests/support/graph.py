"""Scripted nodes, a manual clock and a scripted cancel flag, for the graph tests.

Test doubles, and only that. Each node returns exactly what a test scripts and
records what it was asked. Nothing here resembles an agent - those are Phase 10 -
and nothing here is reachable from ``app/``.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.checkpoint import build_serializer
from app.agents.graph import CancellationProbe, GraphBounds
from app.agents.nodes import DeltaSink, NodeResult, ResearchNodes
from app.agents.runtime import ResearchGraphRunner
from app.agents.schemas import (
    AnswerDraft,
    CitationCheck,
    ClaimItem,
    ContradictionItem,
    CostEstimate,
    Critique,
    EvidenceItem,
    MissingInfo,
    NodeUsage,
    Plan,
    ReportDraft,
    ReportSectionDraft,
    RunBudget,
    SourceRef,
    Subtask,
    SubtaskAssignment,
    TaskOutcome,
    TokenCount,
)
from app.agents.state import ResearchState, RunBrief
from app.core.enums import (
    ClaimStatus,
    ClaimType,
    EvidenceStance,
    ReportSectionKind,
    ResearchMode,
    SourceType,
    TaskPriority,
)

START = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "aether/tests/graph")

Hook = Callable[[], Awaitable[None] | None]


def stable_id(*parts: object) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, "/".join(str(part) for part in parts))


class ManualClock:
    """Time that moves only when a test moves it."""

    def __init__(self, start: datetime = START) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class ScriptedProbe:
    """A cancel flag a test sets."""

    def __init__(self) -> None:
        self.cancelled = False
        self.checks = 0

    def cancel(self) -> None:
        self.cancelled = True

    async def is_cancelled(self, research_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        self.checks += 1
        return self.cancelled


class RecordedRuns:
    """A recorder that keeps what it was asked to project.

    The runner requires one: a graph whose results nobody records is a run that
    produced a checkpoint and nothing a person can read, and making that state
    reachable by forgetting an argument is exactly what a required collaborator
    prevents.
    """

    def __init__(self, *, fails: bool = False) -> None:
        self.states: list[Any] = []
        self._fails = fails

    async def record(self, state: Any) -> object:
        self.states.append(state)
        if self._fails:
            raise RuntimeError("recording failed")
        return len(self.states)

    @property
    def claims(self) -> list[Any]:
        """The claims of the last state recorded."""
        return list(self.states[-1].get("claims") or ()) if self.states else []


@dataclass
class Script:
    subtasks_per_round: int = 2
    sources_per_task: int = 1
    queries_per_task: int = 1
    tokens_per_call: int = 100
    cost_per_call: float = 0.001
    #: Every call reports an unknown price instead of a cost.
    uncosted: bool = False
    #: The round the critic is satisfied on; ``None`` for one that never is.
    sufficient_on_round: int | None = 1
    #: The round on which the planner finds nothing more to research.
    empty_plan_on_round: int | None = None
    #: The round on which the contradiction checker finds one. Needs two
    #: claims to exist, which one round of two subtasks already produces.
    contradictions_on_round: int | None = None
    #: How many drafts the citation validator rejects before passing one.
    citation_failures: int = 0
    #: The answerer returns no answer, as it does for a run with no claims.
    answer_is_empty: bool = False
    research_delay_seconds: float = 0.0
    priorities: tuple[TaskPriority, ...] = ()
    #: Node name -> the call numbers (1-based) that raise.
    fail_on: dict[str, set[int]] = field(default_factory=dict)
    #: Node name -> a specific exception it raises on every call. For the
    #: failures whose *class* is what the graph routes on, rather than the
    #: fact of a failure - a budget refusal, most of all.
    raises: dict[str, Exception] = field(default_factory=dict)
    #: Node names whose calls never return.
    hang: set[str] = field(default_factory=set)
    #: Node name -> a hook run after that node's call, before it returns.
    after: dict[str, Hook] = field(default_factory=dict)


class ScriptedNodes:
    """One object playing every node, so a test reads one script."""

    def __init__(self, script: Script | None = None) -> None:
        self.script = script or Script()
        self.calls: Counter[str] = Counter()
        self.assignments: list[SubtaskAssignment] = []
        #: The state each planning round was given, so a test can assert on what
        #: the graph told the planner - the attached corpus, most of all.
        self.plan_states: list[ResearchState] = []
        self.peak_concurrent_research = 0
        self._active_research = 0
        #: Every piece the answerer handed to its sink, in order.
        self.streamed: list[str] = []

    def bundle(self) -> ResearchNodes:
        return ResearchNodes(
            planner=self,
            researcher=self,
            evidence_extractor=self,
            claim_normalizer=self,
            verifier=self,
            contradiction_checker=self,
            critic=self,
            answerer=self,
            synthesizer=self,
            citation_validator=self,
        )

    # --- script mechanics --------------------------------------------------------

    async def _begin(self, name: str) -> None:
        self.calls[name] += 1
        if name in self.script.hang:
            await asyncio.sleep(3600)
        scripted = self.script.raises.get(name)
        if scripted is not None:
            raise scripted
        if self.calls[name] in self.script.fail_on.get(name, set()):
            raise RuntimeError(f"scripted failure in {name}")

    async def _end(self, name: str) -> None:
        hook = self.script.after.get(name)
        if hook is not None:
            outcome = hook()
            if inspect.isawaitable(outcome):
                await outcome

    def _usage(self, *, queries: int = 0) -> NodeUsage:
        cost = (
            CostEstimate(uncosted_calls=1)
            if self.script.uncosted
            else CostEstimate(usd=self.script.cost_per_call)
        )
        return NodeUsage(
            tokens=TokenCount(prompt_tokens=self.script.tokens_per_call),
            cost=cost,
            search_queries=queries,
        )

    # --- nodes ----------------------------------------------------------------------

    async def plan(self, state: ResearchState) -> NodeResult[Plan]:
        self.plan_states.append(state)
        await self._begin("planner")
        iteration = state.get("iteration", 0) + 1
        count = (
            0 if self.script.empty_plan_on_round == iteration else self.script.subtasks_per_round
        )
        priorities = self.script.priorities or (TaskPriority.MEDIUM,)
        subtasks = tuple(
            Subtask(
                key=f"round{iteration}-task{index}",
                question=f"Question {index} of round {iteration}",
                priority=priorities[index % len(priorities)],
                iteration=iteration,
            )
            for index in range(count)
        )
        result = NodeResult(
            Plan(research_goal=state["query"], iteration=iteration, subtasks=subtasks),
            self._usage(),
        )
        await self._end("planner")
        return result

    async def research(self, assignment: SubtaskAssignment) -> NodeResult[TaskOutcome]:
        self.assignments.append(assignment)
        self._active_research += 1
        self.peak_concurrent_research = max(self.peak_concurrent_research, self._active_research)
        try:
            await self._begin("researcher")
            if self.script.research_delay_seconds:
                await asyncio.sleep(self.script.research_delay_seconds)
        finally:
            self._active_research -= 1
        key = assignment.subtask.key
        sources = tuple(
            SourceRef(
                source_id=stable_id("source", key, index),
                task_key=key,
                title=f"Source {index} for {key}",
                url=f"https://example.com/{key}/{index}",
                source_type=SourceType.WEB,
                publisher="example.com",
                chunk_count=index + 3,
            )
            for index in range(self.script.sources_per_task)
        )
        queries = tuple(f"query {index} for {key}" for index in range(self.script.queries_per_task))
        result = NodeResult(
            TaskOutcome(
                task_key=key,
                iteration=assignment.subtask.iteration,
                sources=sources,
                queries=queries,
            ),
            self._usage(queries=self.script.queries_per_task),
        )
        await self._end("researcher")
        return result

    async def extract(self, state: ResearchState) -> NodeResult[tuple[EvidenceItem, ...]]:
        await self._begin("evidence_extractor")
        iteration = state.get("iteration", 0)
        prefix = f"round{iteration}-"
        items = tuple(
            EvidenceItem(
                id=stable_id("evidence", source.source_id),
                task_key=source.task_key,
                iteration=iteration,
                source_id=source.source_id,
                document_id=stable_id("document", source.source_id),
                claim_text=f"A finding from {source.title}",
                span_start=0,
                # An evidence span's offsets must bracket exactly its text
                # (Phase 10): the citation validator re-reads the document at
                # them, and a span that is not there is not checkable.
                span_end=len(f"A finding from {source.title}"),
                stance=EvidenceStance.SUPPORTS,
            )
            for source in state.get("sources", [])
            if source.task_key.startswith(prefix)
        )
        result = NodeResult(items, self._usage())
        await self._end("evidence_extractor")
        return result

    async def normalize(self, state: ResearchState) -> NodeResult[tuple[ClaimItem, ...]]:
        await self._begin("claim_normalizer")
        known = {claim.id for claim in state.get("claims", [])}
        claims = tuple(
            ClaimItem(
                id=stable_id("claim", item.id),
                normalized_key=f"finding.{item.id}",
                text=item.claim_text,
                claim_type=ClaimType.QUALITATIVE,
                confidence=0.5,
                evidence_ids=(item.id,),
            )
            for item in state.get("evidence", [])
            if stable_id("claim", item.id) not in known
        )
        result = NodeResult(claims, self._usage())
        await self._end("claim_normalizer")
        return result

    async def verify(self, state: ResearchState) -> NodeResult[tuple[ClaimItem, ...]]:
        await self._begin("verifier")
        claims = tuple(
            claim.model_copy(update={"status": ClaimStatus.VERIFIED, "confidence": 0.8})
            for claim in state.get("claims", [])
        )
        result = NodeResult(claims, self._usage())
        await self._end("verifier")
        return result

    async def find_contradictions(
        self, state: ResearchState
    ) -> NodeResult[tuple[ContradictionItem, ...]]:
        await self._begin("contradiction_checker")
        claims = list(state.get("claims", []))
        found: tuple[ContradictionItem, ...] = ()
        if self.script.contradictions_on_round == state.get("iteration", 0) and len(claims) >= 2:
            found = (
                ContradictionItem(
                    id=stable_id("contradiction", claims[0].id, claims[1].id),
                    normalized_key=claims[0].normalized_key,
                    claim_a_id=claims[0].id,
                    claim_b_id=claims[1].id,
                    likely_reason="The two sources measured different periods.",
                ),
            )
        result: NodeResult[tuple[ContradictionItem, ...]] = NodeResult(found, self._usage())
        await self._end("contradiction_checker")
        return result

    async def critique(self, state: ResearchState) -> NodeResult[Critique]:
        await self._begin("critic")
        iteration = state.get("iteration", 0)
        target = self.script.sufficient_on_round
        sufficient = target is not None and iteration >= target
        critique = Critique(
            iteration=iteration,
            sufficient=sufficient,
            missing=()
            if sufficient
            else (
                MissingInfo(description=f"Pricing detail still missing after round {iteration}"),
            ),
        )
        result = NodeResult(critique, self._usage())
        await self._end("critic")
        return result

    async def answer(
        self, state: ResearchState, *, on_delta: DeltaSink
    ) -> NodeResult[AnswerDraft | None]:
        await self._begin("answerer")
        claims = list(state.get("claims") or ())
        if self.script.answer_is_empty or not claims:
            result: NodeResult[AnswerDraft | None] = NodeResult(None, self._usage())
            await self._end("answerer")
            return result
        text = f"Scripted answer from {len(claims)} claims [1]."
        # Delivered in pieces, because a test of the stream has to see more than
        # one: an implementation that published the whole answer once would pass
        # every assertion about the final text.
        for piece in (text[: len(text) // 2], text[len(text) // 2 :]):
            await on_delta(piece)
            self.streamed.append(piece)
        result = NodeResult(
            AnswerDraft(
                text=text,
                model="scripted-model",
                claim_ids=(claims[0].id,),
            ),
            self._usage(),
        )
        await self._end("answerer")
        return result

    async def synthesize(self, state: ResearchState) -> NodeResult[ReportDraft]:
        await self._begin("synthesizer")
        claims = state.get("claims", [])
        draft = ReportDraft(
            title="Scripted report",
            # Deliberately wrong: the graph owns the revision and the caveat.
            revision=0,
            coverage_caveat=None,
            sections=(
                ReportSectionDraft(
                    kind=ReportSectionKind.EXECUTIVE_SUMMARY,
                    heading="Summary",
                    content_md=f"{len(claims)} claims were gathered.",
                    claim_ids=tuple(claim.id for claim in claims[:10]),
                ),
            ),
        )
        result = NodeResult(draft, self._usage())
        await self._end("synthesizer")
        return result

    async def validate(self, state: ResearchState) -> NodeResult[CitationCheck]:
        await self._begin("citation_validator")
        report = state["report"]
        assert report is not None
        if self.calls["citation_validator"] <= self.script.citation_failures:
            check = CitationCheck(
                revision=report.revision,
                checked=1,
                valid=0,
                rejected=1,
                repair_instructions="Cite the claim behind the summary sentence.",
            )
        else:
            check = CitationCheck(revision=report.revision, checked=1, valid=1, rejected=0)
        result = NodeResult(check, self._usage())
        await self._end("citation_validator")
        return result


def budget(**overrides: object) -> RunBudget:
    values: dict[str, object] = {
        "max_iterations": 4,
        "max_sources": 50,
        "max_search_queries": 30,
        "max_runtime_seconds": 300,
        "max_cost_usd": 2.0,
    }
    values.update(overrides)
    return RunBudget.model_validate(values)


def brief(
    *,
    mode: ResearchMode = ResearchMode.DEEP,
    research_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    **budget_overrides: object,
) -> RunBrief:
    return RunBrief(
        research_id=research_id or uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
        query="How do AI inference providers price hosted models?",
        mode=mode,
        depth=1 if mode is ResearchMode.QUICK else 3,
        budget=budget(**budget_overrides),
    )


def make_runner(
    nodes: ScriptedNodes,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    probe: CancellationProbe | None = None,
    clock: ManualClock | None = None,
    recorder: RecordedRuns | None = None,
    width: int = 8,
    concurrency: int = 4,
    node_timeout: float = 5.0,
) -> tuple[ResearchGraphRunner, Any, ManualClock]:
    """A runner over scripted nodes. The in-memory saver uses the production serializer."""
    scripted_probe = probe or ScriptedProbe()
    manual_clock = clock or ManualClock()
    runner = ResearchGraphRunner(
        nodes=nodes.bundle(),
        checkpointer=checkpointer or InMemorySaver(serde=build_serializer()),
        probe=scripted_probe,
        bounds=GraphBounds(
            max_subtasks_per_iteration=width,
            max_concurrency=concurrency,
            node_timeout_seconds=node_timeout,
        ),
        recorder=recorder or RecordedRuns(),
        now=manual_clock,
    )
    return runner, scripted_probe, manual_clock
