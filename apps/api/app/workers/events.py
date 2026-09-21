"""The run's progress stream, derived from the graph's supersteps (Phase 14).

Phase 13 made a worker move a run's *row*: a status, a bar and four counts,
written once per node boundary. That is enough to render a header and not
nearly enough to watch a run happen. This turns the same boundaries into the
event vocabulary the frontend has rendered since Phase 1 - which sources were
found, which claims came out of them, when the critic asked for another round.

**Everything here is observed, never predicted.** LangGraph reports a superstep
after it finishes, so every event is emitted from state that already contains
the work it describes. Nothing is announced because it is about to happen: a
subtask is reported when its outcome exists, a second round when the plan for
it exists, and the reason the critic gave is read from the critique rather than
guessed at. The cost is that an event lags its work by one node; the benefit is
that the stream never describes something that did not happen.

**The answer is the one exception, and it is not really one.** Its pieces are
published while the node is still running, because the text arriving is the
work - an answer announced after it was finished would be a page appearing at
once, which is the thing streaming it exists to avoid. It keeps the property
that matters: nothing is *predicted*. A piece is published because those
characters have already been generated. ``answer_completed`` is still emitted at
the node boundary from checkpointed state, so it is idempotent and replays
correctly, and it carries the whole text - which is what a reader who arrives
after the run has finished is served.

**The cursor is the stream itself.** What has already been announced is
rebuilt, on the first step, from the events the run has already emitted
(``EventBroker.history``) - which is also the durable log. That is what makes a
resumed run pick up where the stream stopped instead of re-announcing forty
sources, and it needs no extra state of its own: the record of what a client
was told *is* the record of what was told.

**A broken stream never breaks a run.** Every emission is best-effort and
logged on failure. The run's row is the system of record for progress; this is
how a person watches it, and watching must not be able to stop the work.
"""

from __future__ import annotations

import contextlib
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from sqlalchemy import select

from app.agents.schemas import (
    AnswerDraft,
    ClaimItem,
    ContradictionItem,
    EvidenceItem,
    GraphNode,
    SourceRef,
    StopReason,
)
from app.agents.state import ResearchState, stop_reason
from app.core.enums import ClaimStatus, ResearchEventType, RunStatus
from app.core.logging import get_logger
from app.db.models.report import ReportRow
from app.db.session import Database
from app.research.events import EventBroker, EventDraft, ResearchEvent
from app.workers.progress import in_order, status_for

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReportFacts:
    """What ``report_completed`` says about the report a reader will open.

    Read back from the row rather than taken from the draft, and that is the
    point: the assembled report has sections the draft never had - Evidence and
    References are built from rows - so a word count taken from the draft would
    not be the count on the page. An event that announces a report the reader
    cannot yet open, or describes a different one, is worse than no event.
    """

    report_id: uuid.UUID
    word_count: int
    overall_confidence: float | None
    coverage_caveat: str | None


class ReportFactsReader(Protocol):
    """Where ``report_completed`` gets its numbers. A protocol so the worker
    depends on the question, not on a table."""

    async def facts_for(self, run_id: uuid.UUID) -> ReportFacts | None: ...


class StoredReportFacts:
    """Reads the four columns the event needs, and nothing else.

    Not ``ReportStore.report_for``: that returns the report with every section
    and every citation, which is a lot of markdown to load in order to
    announce a word count.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def facts_for(self, run_id: uuid.UUID) -> ReportFacts | None:
        statement = select(
            ReportRow.id,
            ReportRow.word_count,
            ReportRow.overall_confidence,
            ReportRow.coverage_caveat,
        ).where(ReportRow.run_id == run_id)
        async with self._database.session() as session:
            row = (await session.execute(statement)).one_or_none()
        if row is None:
            return None
        return ReportFacts(
            report_id=row.id,
            word_count=row.word_count,
            overall_confidence=(
                None if row.overall_confidence is None else float(row.overall_confidence)
            ),
            coverage_caveat=row.coverage_caveat,
        )


@dataclass
class _Announced:
    """What this run's stream has already said, so nothing is said twice."""

    sources: set[uuid.UUID] = field(default_factory=set)
    claims: set[uuid.UUID] = field(default_factory=set)
    contradictions: set[uuid.UUID] = field(default_factory=set)
    subtasks: set[str] = field(default_factory=set)
    searches: set[tuple[str, str]] = field(default_factory=set)
    plans: set[int] = field(default_factory=set)
    critiques: set[int] = field(default_factory=set)
    verification: bool = False
    #: The answer is announced complete once per run. Not per revision: the
    #: repair loop rewrites the report, never the answer.
    answers: int = 0
    #: Counted rather than flagged: a repaired draft is synthesised and checked
    #: a second time, and the revision number is what says which one this is.
    syntheses: int = 0
    citation_checks: int = 0
    terminal: bool = False

    @classmethod
    def of(cls, events: Iterable[ResearchEvent]) -> _Announced:
        """Rebuild from the events already emitted for this run."""
        seen = cls()
        for event in events:
            payload = event.payload
            match event.type:
                case ResearchEventType.SOURCE_FOUND:
                    _add_uuid(seen.sources, payload.get("source_id"))
                case ResearchEventType.CLAIM_EXTRACTED:
                    _add_uuid(seen.claims, payload.get("claim_id"))
                case ResearchEventType.CONTRADICTION_FOUND:
                    _add_uuid(seen.contradictions, payload.get("contradiction_id"))
                case ResearchEventType.SUBTASK_STARTED:
                    _add_str(seen.subtasks, payload.get("task_external_id"))
                case ResearchEventType.SEARCH_STARTED:
                    task = payload.get("task_external_id")
                    query = payload.get("query")
                    if isinstance(task, str) and isinstance(query, str):
                        seen.searches.add((task, query))
                case ResearchEventType.PLANNER_COMPLETED:
                    _add_int(seen.plans, payload.get("iteration"))
                case ResearchEventType.CRITIC_STARTED:
                    _add_int(seen.critiques, payload.get("iteration"))
                case ResearchEventType.VERIFICATION_STARTED:
                    seen.verification = True
                case ResearchEventType.ANSWER_COMPLETED:
                    seen.answers += 1
                case ResearchEventType.SYNTHESIS_STARTED:
                    seen.syntheses += 1
                case ResearchEventType.CITATION_CHECK:
                    seen.citation_checks += 1
                case _:
                    pass
            if event.type.is_terminal:
                seen.terminal = True
        return seen


class BrokerAnswerStream:
    """The answer's text, on its way to whoever is watching the run.

    An ``AnswerStream`` (``app.agents.nodes``) backed by the run's event bus.
    Two decisions are worth stating, because both are about the fact that these
    events are *stored*:

    **Pieces are accumulated, not forwarded.** A provider emits a few characters
    at a time, and publishing each one would be a database row per token - a few
    thousand inserts to deliver text nobody can read that fast. So text
    accumulates and is published in phrase-sized pieces, or sooner when a slow
    model has kept the buffer waiting. ``finish`` publishes the tail, which is
    what stops a short answer - one that never fills the buffer at all - being
    streamed as nothing at all.

    **``answer_started`` goes out with the first piece, not before it.** An
    answer that turns out to have nothing to say never announces itself, so a
    client is never told to clear its screen for text that is not coming.

    **Nothing here may stop the run.** A publish that fails is logged and
    dropped: the answer's record is the value the node returns and the row the
    run is recorded onto, never this. Watching must not be able to break the
    work - and an answer that reached the database but not the screen is
    recoverable, while the reverse is not.
    """

    def __init__(
        self,
        *,
        broker: EventBroker,
        run_id: uuid.UUID,
        chunk_chars: int = 48,
        max_delay_seconds: float = 0.25,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._broker = broker
        self._run_id = run_id
        self._chunk_chars = max(1, chunk_chars)
        self._max_delay = max_delay_seconds
        self._clock = clock
        self._buffer: list[str] = []
        self._pending = 0
        self._index = 0
        self._opened = False
        self._flushed_at = clock()

    async def write(self, text: str) -> None:
        if not text:
            return
        self._buffer.append(text)
        self._pending += len(text)
        if (
            self._pending >= self._chunk_chars
            or self._clock() - self._flushed_at >= self._max_delay
        ):
            await self._flush()

    async def finish(self) -> None:
        """Publish whatever is still held. Called once, on the way out."""
        await self._flush()

    async def _flush(self) -> None:
        if not self._pending:
            return
        if not self._opened:
            # The reader is told to clear whatever is on screen at the moment
            # there is something to replace it with. That is what makes a second
            # attempt correct: a worker that crashed halfway leaves a partial
            # answer behind, and the retry writes a different one over it.
            self._opened = True
            await self._publish(ResearchEventType.ANSWER_STARTED, {})
        piece = "".join(self._buffer)
        self._buffer.clear()
        self._pending = 0
        self._flushed_at = self._clock()
        self._index += 1
        await self._publish(ResearchEventType.ANSWER_DELTA, {"index": self._index, "text": piece})

    async def _publish(self, event_type: ResearchEventType, payload: dict[str, Any]) -> None:
        try:
            await self._broker.publish(
                EventDraft(
                    type=event_type,
                    run_id=self._run_id,
                    status=RunStatus.SYNTHESIZING,
                    payload=payload,
                )
            )
        except Exception as exc:
            logger.warning(
                "a piece of the answer could not be published",
                extra={
                    "run_id": str(self._run_id),
                    "type": event_type.value,
                    "error": str(exc),
                },
            )


class RunEventEmitter:
    """One run's progress stream. Told what the graph did, it says what happened."""

    def __init__(self, *, broker: EventBroker, run_id: uuid.UUID) -> None:
        self._broker = broker
        self._run_id = run_id
        self._seen: _Announced | None = None

    def answer_stream(
        self, *, chunk_chars: int = 48, max_delay_seconds: float = 0.25
    ) -> BrokerAnswerStream:
        """Where this run's answer goes while it is being written.

        Built here rather than by the caller so that the broker and the run id
        are wired in one place - the same two values every other event on this
        run is published with.
        """
        return BrokerAnswerStream(
            broker=self._broker,
            run_id=self._run_id,
            chunk_chars=chunk_chars,
            max_delay_seconds=max_delay_seconds,
        )

    async def prime(self) -> None:
        """Load what has already been streamed for this run.

        Called before the graph starts. A first attempt finds nothing; a
        resumed one finds everything the previous worker announced, which is
        exactly the set this must not announce again.
        """
        if self._seen is not None:
            return
        try:
            history = await self._broker.history(self._run_id)
        except Exception as exc:
            # An empty cursor re-announces, which is noisy but not wrong. A
            # failed prime that stopped the run would be.
            logger.warning(
                "could not read a run's streamed events; it may repeat some",
                extra={"run_id": str(self._run_id), "error": str(exc)},
            )
            self._seen = _Announced()
            return
        self._seen = _Announced.of(history)

    # --- one superstep ----------------------------------------------------

    async def stepped(self, nodes: tuple[GraphNode, ...], state: ResearchState) -> None:
        """Announce whatever the nodes of this superstep produced."""
        await self.prime()
        for node in in_order(nodes):
            try:
                await self._for_node(node, state)
            except Exception:
                logger.exception(
                    "could not stream a run's progress",
                    extra={"run_id": str(self._run_id), "node": node.value},
                )

    async def _for_node(self, node: GraphNode, state: ResearchState) -> None:
        match node:
            case GraphNode.PLANNER:
                await self._planned(state)
            case GraphNode.RESEARCHER:
                await self._researched(state)
            case GraphNode.EVIDENCE_EXTRACTOR:
                await self._progress(state, status_for(node))
            case GraphNode.CLAIM_NORMALIZER:
                await self._normalised(state)
            case GraphNode.VERIFIER:
                await self._progress(state, status_for(node))
            case GraphNode.CONTRADICTION_CHECKER:
                await self._contradicted(state)
            case GraphNode.CRITIC:
                await self._criticised(state)
            case GraphNode.ANSWERER:
                await self._answered(state)
            case GraphNode.SYNTHESIZER:
                await self._synthesised(state)
            case GraphNode.CITATION_VALIDATOR:
                await self._validated(state)

    # --- the phases -------------------------------------------------------

    async def _planned(self, state: ResearchState) -> None:
        """A plan exists. A second one also means the critic was not satisfied."""
        plan = state.get("research_plan")
        seen = self._cursor()
        if plan is None or plan.iteration in seen.plans:
            return
        seen.plans.add(plan.iteration)

        if plan.iteration > 1:
            critique = state.get("critique")
            await self._emit(
                ResearchEventType.ADDITIONAL_RESEARCH_REQUESTED,
                RunStatus.RESEARCHING,
                {
                    "iteration": plan.iteration,
                    # The critic's own words. Empty rather than invented when
                    # it gave none: a reason the system wrote for itself would
                    # read as the critic's judgement.
                    "reason": "" if critique is None else critique.rationale,
                    "new_task_count": len(plan.subtasks),
                },
            )
            budget = state.get("budget")
            await self._emit(
                ResearchEventType.ITERATION_STARTED,
                RunStatus.RESEARCHING,
                {
                    "iteration": plan.iteration,
                    "max_iterations": 0 if budget is None else budget.max_iterations,
                },
            )

        await self._emit(
            ResearchEventType.PLANNER_STARTED,
            RunStatus.PLANNING,
            {"iteration": plan.iteration},
        )
        await self._emit(
            ResearchEventType.PLANNER_COMPLETED,
            RunStatus.RESEARCHING,
            {
                "research_goal": plan.research_goal,
                "iteration": plan.iteration,
                "tasks": [
                    {
                        "external_id": task.key,
                        "question": task.question,
                        "priority": task.priority.value,
                        "rationale": task.rationale,
                    }
                    for task in plan.subtasks
                ],
            },
        )

    async def _researched(self, state: ResearchState) -> None:
        """Subtasks that ran, searches they issued, and the sources they read."""
        seen = self._cursor()
        for outcome in state.get("completed_tasks") or ():
            if outcome.task_key not in seen.subtasks:
                seen.subtasks.add(outcome.task_key)
                await self._emit(
                    ResearchEventType.SUBTASK_STARTED,
                    RunStatus.RESEARCHING,
                    {
                        "task_external_id": outcome.task_key,
                        "question": _question_for(state, outcome.task_key),
                    },
                )
            for query in outcome.queries:
                if (outcome.task_key, query) in seen.searches:
                    continue
                seen.searches.add((outcome.task_key, query))
                await self._emit(
                    ResearchEventType.SEARCH_STARTED,
                    RunStatus.RESEARCHING,
                    {
                        "task_external_id": outcome.task_key,
                        "query": query,
                        # The channel that answered, not the vendor behind it:
                        # which search API served a web query is recorded on
                        # the tool call, where failover between two of them is
                        # also visible.
                        "provider": _channel_for(state, outcome.task_key),
                    },
                )

        sources = state.get("sources") or []
        fresh = [ref for ref in sources if ref.source_id not in seen.sources]
        for ref in fresh:
            seen.sources.add(ref.source_id)
            await self._emit(
                ResearchEventType.SOURCE_FOUND,
                RunStatus.RESEARCHING,
                _source_found(ref),
            )
            await self._emit(
                ResearchEventType.SOURCE_PROCESSED,
                RunStatus.RESEARCHING,
                {
                    "source_id": str(ref.source_id),
                    "title": ref.title,
                    "chunk_count": ref.chunk_count,
                    # Ingestion collapses two researchers finding one page onto
                    # one source, so a duplicate never reaches state as a
                    # second row. Near-duplicate clustering is a projection the
                    # run has not made yet (Phase 11), so this is honestly null
                    # rather than optimistically self-referential.
                    "duplicate_of": None,
                },
            )
        if fresh:
            budget = state.get("budget")
            await self._emit(
                ResearchEventType.SOURCES_PROGRESS,
                RunStatus.RESEARCHING,
                {
                    # A source exists in state only once it has been fetched,
                    # parsed and ingested, so discovered and processed are the
                    # same number here - and saying so is more honest than
                    # inventing a gap between them.
                    "processed": len(sources),
                    "discovered": len(sources),
                    "limit": 0 if budget is None else budget.max_sources,
                },
            )

    async def _normalised(self, state: ResearchState) -> None:
        """Claims exist. The verifier is what reads them next."""
        seen = self._cursor()
        evidence = {item.id: item for item in state.get("evidence") or ()}
        claims = state.get("claims") or []
        for claim in claims:
            if claim.id in seen.claims:
                continue
            seen.claims.add(claim.id)
            await self._emit(
                ResearchEventType.CLAIM_EXTRACTED,
                RunStatus.RESEARCHING,
                _claim_extracted(claim, evidence),
            )
        await self._progress(state, RunStatus.RESEARCHING)

        if claims and not seen.verification:
            seen.verification = True
            await self._emit(
                ResearchEventType.VERIFICATION_STARTED,
                RunStatus.VERIFYING,
                {"claim_count": len(claims)},
            )

    async def _contradicted(self, state: ResearchState) -> None:
        seen = self._cursor()
        claims = {claim.id: claim for claim in state.get("claims") or ()}
        for contradiction in state.get("contradictions") or ():
            if contradiction.id in seen.contradictions:
                continue
            seen.contradictions.add(contradiction.id)
            await self._emit(
                ResearchEventType.CONTRADICTION_FOUND,
                RunStatus.VERIFYING,
                _contradiction_found(contradiction, claims),
            )
        await self._progress(state, RunStatus.VERIFYING)

    async def _criticised(self, state: ResearchState) -> None:
        critique = state.get("critique")
        seen = self._cursor()
        if critique is None or critique.iteration in seen.critiques:
            return
        seen.critiques.add(critique.iteration)
        await self._emit(
            ResearchEventType.CRITIC_STARTED,
            RunStatus.VERIFYING,
            {"iteration": critique.iteration},
        )

    async def _answered(self, state: ResearchState) -> None:
        """The answer is finished. Said once, with the whole text.

        The text is repeated here even though every character of it has already
        been streamed, and that is the point: a reader who opens the page after
        the run finished never saw a delta, and this event is what the replayed
        stream hands them. It is also what the frontend reconciles its
        accumulated text against, so a dropped piece heals rather than leaving a
        gap in the middle of a sentence.
        """
        answer = state.get("answer")
        seen = self._cursor()
        if answer is None or seen.answers:
            return
        seen.answers += 1
        await self._emit(
            ResearchEventType.ANSWER_COMPLETED,
            RunStatus.SYNTHESIZING,
            _answer_completed(answer),
        )

    async def _synthesised(self, state: ResearchState) -> None:
        draft = state.get("report")
        seen = self._cursor()
        # One announcement per revision: revision 0 is the first draft, so the
        # count of announcements so far must not exceed it.
        if draft is None or seen.syntheses > draft.revision:
            return
        seen.syntheses += 1
        await self._emit(
            ResearchEventType.SYNTHESIS_STARTED,
            RunStatus.SYNTHESIZING,
            {"section_count": len(draft.sections)},
        )

    async def _validated(self, state: ResearchState) -> None:
        check = state.get("citation_check")
        seen = self._cursor()
        if check is None or seen.citation_checks > check.revision:
            return
        seen.citation_checks += 1
        await self._emit(
            ResearchEventType.CITATION_CHECK,
            RunStatus.VALIDATING,
            {"checked": check.checked, "valid": check.valid, "rejected": check.rejected},
        )

    async def _progress(self, state: ResearchState, status: RunStatus) -> None:
        """The three counters the evidence stage renders."""
        claims = state.get("claims") or []
        await self._emit(
            ResearchEventType.EVIDENCE_PROGRESS,
            status,
            {
                "claims": len(claims),
                "evidence": len(state.get("evidence") or []),
                "verified": sum(1 for claim in claims if claim.status is ClaimStatus.VERIFIED),
            },
        )

    # --- how a run ends ---------------------------------------------------

    async def completed(self, state: ResearchState, facts: ReportFacts | None) -> None:
        """The report is written, validated and readable."""
        if facts is None:
            # A run can return without a report - cancelled, or stopped by a
            # limit before synthesis. There is nothing to announce, and an
            # event pointing at a report that does not exist would send the
            # reader to a 404.
            logger.info(
                "a run finished with no stored report to announce",
                extra={"run_id": str(self._run_id)},
            )
            return
        cost = state.get("estimated_cost")
        await self._emit(
            ResearchEventType.REPORT_COMPLETED,
            RunStatus.COMPLETED,
            {
                "report_id": str(facts.report_id),
                "word_count": facts.word_count,
                "overall_confidence": facts.overall_confidence,
                "cost_usd": 0.0 if cost is None else round(cost.usd, 6),
                "coverage_caveat": facts.coverage_caveat,
            },
        )

    async def cancelled(self, state: ResearchState) -> None:
        """A cancellation the worker observed, if nobody has announced it yet.

        The API emits this when the user asks, which is the usual path and the
        reason this checks. The worker emits it for the other one: a run
        cancelled by something that did not go through the service still stops,
        and the stream should say so rather than simply going quiet.
        """
        if stop_reason(state) is not StopReason.CANCELLED:
            return
        await self._announce_end(
            ResearchEventType.RESEARCH_CANCELLED,
            RunStatus.CANCELLED,
            {"cancelled_by": "system"},
        )

    async def failed(self, *, code: str, message: str, partial_report: bool) -> None:
        """The run stopped and will not be retried."""
        await self._announce_end(
            ResearchEventType.RESEARCH_FAILED,
            RunStatus.FAILED,
            {"code": code, "message": message, "partial_report": partial_report},
        )

    async def _announce_end(
        self, event_type: ResearchEventType, status: RunStatus, payload: dict[str, Any]
    ) -> None:
        """Emit a terminal event, unless one has already been emitted.

        The cursor is not enough here. It was built when the run started, and
        the usual way a run ends early is that somebody *else* ended it while
        it was running - a user cancelling through the API, which publishes the
        terminal event itself. Asking the log again is one query on a path
        taken once per run, and it is what stops a client being told twice that
        its run stopped.
        """
        if await self._already_ended():
            return
        self._cursor().terminal = True
        await self._emit(event_type, status, payload)

    async def _already_ended(self) -> bool:
        try:
            history = await self._broker.history(self._run_id)
        except Exception as exc:
            # Fall back to what this emitter knows. A duplicate terminal event
            # closes the client's stream twice, which it recovers from; a
            # missing one leaves it open on a run that has stopped.
            logger.warning(
                "could not check whether a run's end was already announced",
                extra={"run_id": str(self._run_id), "error": str(exc)},
            )
            return self._cursor().terminal
        return any(event.type.is_terminal for event in history)

    # --- internals --------------------------------------------------------

    def _cursor(self) -> _Announced:
        if self._seen is None:  # pragma: no cover - prime() precedes every path
            self._seen = _Announced()
        return self._seen

    async def _emit(
        self, event_type: ResearchEventType, status: RunStatus, payload: dict[str, Any]
    ) -> None:
        try:
            await self._broker.publish(
                EventDraft(type=event_type, run_id=self._run_id, status=status, payload=payload)
            )
        except Exception as exc:
            logger.warning(
                "a progress event could not be published",
                extra={
                    "run_id": str(self._run_id),
                    "type": event_type.value,
                    "error": str(exc),
                },
            )


# --- payload shapes (mirrors ``ResearchEvent`` in @aether/shared-types) --------


def _answer_completed(answer: AnswerDraft) -> dict[str, Any]:
    return {
        "text": answer.text,
        "model": answer.model,
        "word_count": answer.word_count,
        "citation_count": len(answer.claim_ids),
        "truncated": answer.truncated,
    }


def _source_found(ref: SourceRef) -> dict[str, Any]:
    return {
        "source_id": str(ref.source_id),
        "title": ref.title,
        "url": ref.url,
        "publisher": ref.publisher,
        "source_type": ref.source_type.value,
        # Nothing in the pipeline scores a source's relevance yet, and the
        # contract distinguishes "not measured" from a low score.
        "relevance_score": None,
        "task_external_id": ref.task_key,
    }


def _claim_extracted(
    claim: ClaimItem, evidence: Mapping[uuid.UUID, EvidenceItem]
) -> dict[str, Any]:
    """A claim, attributed to the first source that evidences it.

    A claim can rest on spans from several sources; the event names one, which
    is what the activity feed shows beside it. The whole set is on
    ``/evidence``, and the event is a notification rather than the record.
    """
    first = next((evidence[eid] for eid in claim.evidence_ids if eid in evidence), None)
    return {
        "claim_id": str(claim.id),
        "text": claim.text,
        "confidence": claim.confidence,
        "source_id": None if first is None else str(first.source_id),
        "task_external_id": None if first is None else first.task_key,
    }


def _contradiction_found(
    contradiction: ContradictionItem, claims: Mapping[uuid.UUID, ClaimItem]
) -> dict[str, Any]:
    """The two values that disagree, taken from the claims themselves.

    ``object_value`` is the claim's own words for what it asserts, extracted
    verbatim by the normalizer. Falling back to the claim text keeps the event
    readable when a claim has no isolated value - a qualitative disagreement
    has two statements rather than two numbers.
    """
    return {
        "contradiction_id": str(contradiction.id),
        "normalized_key": contradiction.normalized_key,
        "value_a": _value_of(claims.get(contradiction.claim_a_id)),
        "value_b": _value_of(claims.get(contradiction.claim_b_id)),
        "likely_reason": contradiction.likely_reason,
    }


def _value_of(claim: ClaimItem | None) -> str:
    if claim is None:
        return ""
    return claim.object_value or claim.text


def _question_for(state: ResearchState, task_key: str) -> str:
    for subtask in state.get("subtasks") or ():
        if subtask.key == task_key:
            return subtask.question
    return ""


def _channel_for(state: ResearchState, task_key: str) -> str:
    for subtask in state.get("subtasks") or ():
        if subtask.key == task_key:
            return subtask.channel.value
    return ""


def _add_uuid(target: set[uuid.UUID], value: object) -> None:
    if isinstance(value, str):
        with contextlib.suppress(ValueError):
            target.add(uuid.UUID(value))


def _add_str(target: set[str], value: object) -> None:
    if isinstance(value, str):
        target.add(value)


def _add_int(target: set[int], value: object) -> None:
    if isinstance(value, int):
        target.add(value)
