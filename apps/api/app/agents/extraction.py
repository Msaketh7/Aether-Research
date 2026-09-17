"""Evidence extraction and claim normalization: text into checkable statements.

This is where the product's central promise is either kept or quietly broken, so
two rules are enforced by the agent rather than asked of the model.

**A quote must be found before it is evidence.** The extractor names a passage
and quotes it; this looks the quote up in that passage's text, character for
character. Found, and the span's offsets into the stored document are computed
from where it was found - never reported by the model. Not found, and it is
dropped with a counted reason. A paraphrase has no offsets and an invention has
no source, and neither can be shown to a reader as proof of anything.

**A claim must rest on evidence that exists.** The normalizer cites evidence by
catalogue number. Numbers that name nothing are dropped, and a claim left with
no evidence at all is dropped with it - ``ClaimItem`` will not hold one, because
a claim with nothing behind it could only ever be cited to nothing.

Claim identity is derived, not generated: a claim's id is a UUID5 over the run
and its normalized key. So the same assertion found again in a later round is
the *same* claim, the state's reducer replaces it rather than storing a second
copy, and the evidence behind it accumulates instead of splitting in two.
"""

from __future__ import annotations

import asyncio
import itertools
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from app.agents.base import AgentContext, ModelAgent, total_usage
from app.agents.catalog import (
    MAX_PROMPT_EVIDENCE,
    Catalog,
    render_evidence,
    source_catalog,
)
from app.agents.nodes import NodeResult
from app.agents.outputs import (
    MAX_CLAIMS_PER_CALL,
    MAX_EVIDENCE_PER_CALL,
    ClaimsOutput,
    EvidenceOutput,
)
from app.agents.prompting import render
from app.agents.schemas import (
    MAX_EVIDENCE_PER_CLAIM,
    ClaimItem,
    EvidenceItem,
    NodeUsage,
    SourceRef,
)
from app.agents.state import ResearchState
from app.core.enums import AgentName, ClaimStatus, EvidenceStance
from app.core.logging import get_logger
from app.models.gateway import LLMGateway
from app.retrieval.filters import ChunkFilter, ChunkView
from app.retrieval.retriever import Retriever
from app.sources.untrusted import UntrustedPassage, untrusted_block

logger = get_logger(__name__)

#: Passages one extraction call may read. Twelve chunks is a few thousand words,
#: which is a prompt a medium-tier model reads carefully; thirty is one it skims.
PASSAGES_PER_CALL = 12
#: Extraction calls one round may make. Bounds the cost of a round that gathered
#: a great many sources, and with the batch size above it is also what decides
#: how much of a large round is read at all - so a round that hits it says so.
MAX_EXTRACTION_CALLS = 3
#: Chunks retrieved per subtask before batching. More than will be read, so that
#: deduplication across subtasks does not leave a batch half empty.
CHUNKS_PER_SUBTASK = 8

MAX_EVIDENCE_TOKENS = 8000
MAX_CLAIM_TOKENS = 8000

#: Namespace for derived claim ids. Fixed forever: changing it would make every
#: stored claim a different claim.
_CLAIM_NAMESPACE = uuid.UUID("6f1d5a2e-0b74-4f2a-9a1c-2f1a6b4e7c30")
_KEY_NOISE = re.compile(r"[^a-z0-9|]+")


@dataclass(frozen=True, slots=True)
class _Passage:
    """One retrieved chunk as the extractor sees it, with what it belongs to."""

    chunk: ChunkView
    task_key: str
    iteration: int


class EvidenceAgent(ModelAgent):
    """Quotes the run's documents, verbatim, with offsets that can be re-read."""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        retriever: Retriever,
        passages_per_call: int = PASSAGES_PER_CALL,
        max_calls: int = MAX_EXTRACTION_CALLS,
        chunks_per_subtask: int = CHUNKS_PER_SUBTASK,
        max_output_tokens: int = MAX_EVIDENCE_TOKENS,
    ) -> None:
        super().__init__(
            gateway, role=AgentName.EVIDENCE_EXTRACTOR, max_output_tokens=max_output_tokens
        )
        self._retriever = retriever
        self._batch = passages_per_call
        self._max_calls = max_calls
        self._per_subtask = chunks_per_subtask

    async def extract(self, state: ResearchState) -> NodeResult[tuple[EvidenceItem, ...]]:
        iteration = state.get("iteration", 0)
        context = AgentContext(
            research_id=state["research_id"],
            user_id=state["user_id"],
            mode=state["parameters"].mode,
            iteration=iteration,
        )
        passages = await self._passages(state, iteration)
        if not passages:
            logger.info(
                "nothing was retrieved to extract evidence from",
                extra={"research_id": str(state["research_id"]), "iteration": iteration},
            )
            return NodeResult(value=(), usage=NodeUsage())

        batches = [
            passages[start : start + self._batch] for start in range(0, len(passages), self._batch)
        ][: self._max_calls]
        unread = len(passages) - sum(len(batch) for batch in batches)
        if unread:
            logger.info(
                "more was retrieved than one round of extraction reads",
                extra={
                    "research_id": str(state["research_id"]),
                    "iteration": iteration,
                    "read": len(passages) - unread,
                    "unread": unread,
                },
            )

        results = await asyncio.gather(
            *(self._extract_batch(context, state, batch) for batch in batches)
        )
        evidence = tuple(item for found, _ in results for item in found)
        usage = total_usage([spent for _, spent in results])
        logger.info(
            "evidence extracted",
            extra={
                "research_id": str(state["research_id"]),
                "iteration": iteration,
                "passages": sum(len(batch) for batch in batches),
                "evidence": len(evidence),
            },
        )
        return NodeResult(value=evidence, usage=usage)

    # --- retrieval ------------------------------------------------------------

    async def _passages(self, state: ResearchState, iteration: int) -> list[_Passage]:
        """This round's chunks, one subtask at a time, interleaved.

        Interleaved rather than concatenated so that the batch ceiling cuts
        every subtask equally: eight chunks from the first subtask and nothing
        from the other three is how a round ends up with a report that covers a
        quarter of its plan.
        """
        by_task = _sources_by_task(state, iteration)
        if not by_task:
            return []
        questions = {task.key: task.question for task in state.get("subtasks") or ()}

        per_task = await asyncio.gather(
            *(
                self._for_task(
                    state,
                    task_key,
                    questions.get(task_key, state["query"]),
                    refs,
                    iteration=iteration,
                )
                for task_key, refs in by_task.items()
            )
        )
        # ``zip_longest``, not ``zip``: zip stops at the shortest, so one
        # subtask that retrieved a single chunk would have capped every other
        # subtask at one chunk too, and the rest would have been dropped
        # silently. Found by re-reading rather than by a failure, which is how
        # this class of bug usually arrives.
        interleaved: list[_Passage] = []
        seen: set[uuid.UUID] = set()
        for row in itertools.zip_longest(*per_task):
            for passage in row:
                if passage is None or passage.chunk.id in seen:
                    continue
                seen.add(passage.chunk.id)
                interleaved.append(passage)
        return interleaved

    async def _for_task(
        self,
        state: ResearchState,
        task_key: str,
        question: str,
        refs: Sequence[SourceRef],
        *,
        iteration: int,
    ) -> list[_Passage]:
        """The chunks most relevant to one subtask, from that subtask's sources only.

        Filtered by source rather than searched across the whole run, so a
        subtask's evidence comes from what that subtask found. Attribution is
        the point: ``EvidenceItem.task_key`` is what later tells a reader which
        question a quote was gathered to answer.
        """
        result = await self._retriever.retrieve_with_filters(
            question,
            filters=ChunkFilter(
                run_id=state["research_id"],
                source_ids=tuple(ref.source_id for ref in refs),
            ),
            user_id=state["user_id"],
            limit=self._per_subtask,
        )
        return [
            _Passage(chunk=hit.chunk, task_key=task_key, iteration=iteration)
            for hit in result.chunks
        ]

    # --- one call -------------------------------------------------------------

    async def _extract_batch(
        self,
        context: AgentContext,
        state: ResearchState,
        batch: list[_Passage],
    ) -> tuple[tuple[EvidenceItem, ...], NodeUsage]:
        catalog = Catalog(tuple(batch))
        prompt = render(
            "evidence",
            question=state["query"],
            subtasks=_questions(state, batch),
            passage_count=str(len(batch)),
            passages=_render_passages(catalog),
            max_evidence=str(MAX_EVIDENCE_PER_CALL),
        )
        output, usage = await self.ask(context, prompt=prompt, schema=EvidenceOutput)

        found: list[EvidenceItem] = []
        unquoted = 0
        invented = 0
        for candidate in output.evidence:
            passage = catalog.get(candidate.passage)
            if passage is None:
                invented += 1
                continue
            item = _locate(candidate.quote, passage, candidate.stance)
            if item is None:
                unquoted += 1
                continue
            found.append(item)

        if invented or unquoted:
            logger.warning(
                "extracted evidence was discarded",
                extra={
                    "research_id": str(context.research_id),
                    "iteration": context.iteration,
                    "returned": len(output.evidence),
                    "kept": len(found),
                    "passage_not_offered": invented,
                    "quote_not_found": unquoted,
                },
            )
        return tuple(found), usage


class ClaimNormalizerAgent(ModelAgent):
    """Turns evidence spans into atomic, keyed claims."""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        max_output_tokens: int = MAX_CLAIM_TOKENS,
    ) -> None:
        super().__init__(
            gateway, role=AgentName.CLAIM_NORMALIZER, max_output_tokens=max_output_tokens
        )

    async def normalize(self, state: ResearchState) -> NodeResult[tuple[ClaimItem, ...]]:
        context = AgentContext(
            research_id=state["research_id"],
            user_id=state["user_id"],
            mode=state["parameters"].mode,
            iteration=state.get("iteration", 0),
        )
        sources = source_catalog(state)
        unclaimed = _unclaimed(state)
        if not unclaimed:
            logger.info(
                "no new evidence to normalize",
                extra={
                    "research_id": str(state["research_id"]),
                    "iteration": context.iteration,
                    "evidence": len(state.get("evidence") or ()),
                },
            )
            return NodeResult(value=(), usage=NodeUsage())

        prompt = render(
            "claims",
            question=state["query"],
            existing_keys=_existing_keys(state),
            evidence_count=str(len(unclaimed)),
            evidence=render_evidence(unclaimed, sources=sources),
            max_claims=str(MAX_CLAIMS_PER_CALL),
        )
        output, usage = await self.ask(context, prompt=prompt, schema=ClaimsOutput)

        existing = {claim.id: claim for claim in state.get("claims") or ()}
        claims: dict[uuid.UUID, ClaimItem] = {}
        dropped = 0
        for proposed in output.claims:
            cited, unknown = unclaimed.resolve(list(proposed.evidence))
            if unknown:
                dropped += 1
            if not cited:
                continue
            key = normalize_key(proposed.normalized_key)
            claim_id = claim_identity(state["research_id"], key)
            # A claim found again keeps every span it already had: the reducer
            # replaces by id, so a re-emitted claim listing only this round's
            # evidence would silently drop the last round's.
            previous = claims.get(claim_id) or existing.get(claim_id)
            ids = _merge_evidence(previous, cited)
            claims[claim_id] = ClaimItem(
                id=claim_id,
                normalized_key=key,
                text=proposed.text,
                claim_type=proposed.claim_type,
                status=previous.status if previous else ClaimStatus.CANDIDATE,
                confidence=proposed.confidence,
                evidence_ids=ids,
            )

        logger.info(
            "claims normalized",
            extra={
                "research_id": str(state["research_id"]),
                "iteration": context.iteration,
                "evidence_considered": len(unclaimed),
                "proposed": len(output.claims),
                "kept": len(claims),
                "cited_unknown_evidence": dropped,
            },
        )
        return NodeResult(value=tuple(claims.values()), usage=usage)


# --- helpers ------------------------------------------------------------------


def claim_identity(research_id: uuid.UUID, normalized_key: str) -> uuid.UUID:
    """A claim's id, derived from what it asserts within its run.

    Derived rather than random so the same assertion is the same claim across
    rounds. Scoped to the run so two runs never share one.
    """
    return uuid.uuid5(_CLAIM_NAMESPACE, f"{research_id}:{normalized_key}")


def normalize_key(raw: str) -> str:
    """A grouping key reduced to its content.

    The model is asked for ``subject | predicate | qualifier``; this makes
    "Nvidia | Data Center revenue | FY2025-Q4" and
    "nvidia | data center revenue | fy2025 q4" the same key. Without it, the
    contradiction check joins on formatting and finds nothing.
    """
    parts = [_KEY_NOISE.sub(" ", part.strip().lower()).strip() for part in raw.split("|")]
    return " | ".join(part for part in parts if part)[:300] or raw.strip().lower()[:300]


def _locate(quote: str, passage: _Passage, stance: EvidenceStance) -> EvidenceItem | None:
    """The quote's place in the passage's document, or ``None`` if it is not there.

    Exact match only. A near match - normalised whitespace, a smart quote turned
    straight - would give offsets that point at *almost* the quoted text, and a
    citation a reader cannot reproduce is worse than one that was never made.
    """
    text = passage.chunk.text.expose()
    index = text.find(quote)
    if index < 0:
        return None
    start = passage.chunk.char_start + index
    return EvidenceItem(
        id=uuid.uuid4(),
        task_key=passage.task_key,
        iteration=passage.iteration,
        source_id=passage.chunk.source_id,
        document_id=passage.chunk.document_id,
        claim_text=quote,
        span_start=start,
        span_end=start + len(quote),
        stance=stance,
    )


def _render_passages(catalog: Catalog[_Passage]) -> str:
    """The batch as one delimited data block. This is the hostile input."""
    return untrusted_block(
        [
            UntrustedPassage(
                label=(
                    f"passage {number} | subtask {passage.task_key} "
                    f"| {passage.chunk.text.source_url}"
                ),
                text=passage.chunk.text,
            )
            for number, passage in catalog.numbered()
        ]
    )


def _questions(state: ResearchState, batch: list[_Passage]) -> str:
    """The subtask questions this batch's passages were gathered for."""
    keys = {passage.task_key for passage in batch}
    questions = {
        task.key: task.question for task in state.get("subtasks") or () if task.key in keys
    }
    return "\n".join(f"- [{key}] {question}" for key, question in sorted(questions.items()))


def _sources_by_task(state: ResearchState, iteration: int) -> dict[str, list[SourceRef]]:
    """This round's sources, grouped by the subtask that found them.

    Only this round's: earlier rounds' sources have already been extracted from,
    and re-reading them would pay for the same spans again and produce evidence
    the reducer would merge away by id anyway.
    """
    completed = {
        outcome.task_key
        for outcome in state.get("completed_tasks") or ()
        if outcome.iteration == iteration
    }
    grouped: dict[str, list[SourceRef]] = {}
    for ref in state.get("sources") or ():
        if ref.task_key in completed:
            grouped.setdefault(ref.task_key, []).append(ref)
    return grouped


def _unclaimed(state: ResearchState) -> Catalog[EvidenceItem]:
    """Evidence no claim cites yet, bounded after the filter rather than before.

    Normalizing everything again each round would re-pay for claims that already
    exist. What is new is what needs a claim; what is old already has one, and
    the reducer keeps it.

    The cap is applied to the *unclaimed* evidence, not to the run's evidence:
    filtering a capped catalogue would mean that once a run held more evidence
    than one prompt carries, new spans could fall outside the cap and never be
    normalized at all.
    """
    claimed = {
        evidence_id for claim in state.get("claims") or () for evidence_id in claim.evidence_ids
    }
    fresh = [item for item in state.get("evidence") or () if item.id not in claimed]
    fresh.sort(key=lambda item: (str(item.source_id), item.span_start, str(item.id)))
    return Catalog(tuple(fresh[:MAX_PROMPT_EVIDENCE]))


def _existing_keys(state: ResearchState) -> str:
    """The keys already in use, so a second round groups onto them rather than beside them."""
    keys = sorted({claim.normalized_key for claim in state.get("claims") or ()})
    if not keys:
        return "No claims have been made yet."
    shown = keys[:40]
    listed = "\n".join(f"- {key}" for key in shown)
    more = f"\n- and {len(keys) - len(shown)} more" if len(keys) > len(shown) else ""
    return (
        "Keys already in use. Reuse one exactly when a new claim asserts the same "
        f"thing about the same subject and period:\n{listed}{more}"
    )


def _merge_evidence(
    previous: ClaimItem | None, cited: Sequence[EvidenceItem]
) -> tuple[uuid.UUID, ...]:
    merged = list(previous.evidence_ids) if previous else []
    for item in cited:
        if item.id not in merged:
            merged.append(item.id)
    return tuple(merged[:MAX_EVIDENCE_PER_CLAIM])
