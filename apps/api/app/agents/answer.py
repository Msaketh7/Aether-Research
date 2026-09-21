"""The answerer: the question, answered, while the reader watches it appear.

Every other agent in this system produces material for the *next* agent. This
one produces the thing the person who asked actually reads, and it is the only
call whose latency they experience directly - so it is the only one that
streams.

**Why it is not the synthesizer.** The report answers the question the way a
document does: sections, an evidence list, a reference list, assembled from
rows. That is the right artifact and the wrong first thing to show someone who
asked a question thirty seconds ago. The two also want different models,
different ceilings and different prose, and collapsing them would mean choosing
one of each. They share the material and nothing else.

**It runs before synthesis, deliberately.** The claims are gathered, verified
and numbered by the time this node is reached, and the report is the expensive
step after it. A reader therefore has their answer before the run has finished
paying for the report - which is the entire point of streaming it - and a run
that dies at synthesis has still answered the question.

**Three constraints, the same three the synthesizer has** (``app.agents.synthesis``):

* **Cited claims are read out of the text.** ``claim_ids`` comes from the ``[n]``
  markers the model actually wrote, so it cannot name a claim the prose never
  used or miss one it did (ADR 0015).
* **No claims, no answer.** With nothing to cite there is nothing to say that
  would not be invented, so the node returns nothing rather than a paragraph of
  fluent prose with no basis. The run fails at synthesis, which is the honest
  outcome.
* **The caveat is not this agent's to write.** A limit that ended discovery is a
  fact about the run; it is stored on the run and shown beside the answer, so a
  writer cannot drop it.

**A failure here is not a failure of the run.** The gateway does not retry or
fail over a stream once a token has been delivered, so this can raise with half
an answer already on the reader's screen. The graph records it and carries on to
synthesis: the report is still produced, and the reader still gets a report.
"""

from __future__ import annotations

from uuid import UUID

from app.agents.base import AgentContext, DeltaSink, ModelAgent
from app.agents.catalog import (
    Catalog,
    claim_catalog,
    contradiction_catalog,
    render_claims,
    render_contradictions,
)
from app.agents.citations import cited_numbers
from app.agents.nodes import NodeResult
from app.agents.prompting import render
from app.agents.schemas import (
    MAX_ANSWER_CHARS,
    MAX_CLAIMS_PER_SECTION,
    AnswerDraft,
    ClaimItem,
    ContradictionItem,
)
from app.agents.state import ResearchState
from app.core.enums import AgentName, ClaimStatus
from app.core.logging import get_logger
from app.models.gateway import LLMGateway

logger = get_logger(__name__)

#: Four paragraphs of prose, with room for a short list. An answer is not a
#: report, and a ceiling is the only thing that reliably keeps it from becoming
#: one - the instruction alone does not.
MAX_ANSWER_TOKENS = 1400


class AnswerAgent(ModelAgent):
    """Writes the direct answer, streaming it as it is written."""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        max_output_tokens: int = MAX_ANSWER_TOKENS,
    ) -> None:
        super().__init__(gateway, role=AgentName.ANSWERER, max_output_tokens=max_output_tokens)

    async def answer(
        self, state: ResearchState, *, on_delta: DeltaSink
    ) -> NodeResult[AnswerDraft | None]:
        """Answer the question from the run's claims, or return nothing.

        ``None`` is a real outcome, not a failure: a run whose researchers found
        nothing has no claims, and an answer written from no claims is the one
        artifact this system exists not to produce.
        """
        claims = claim_catalog(state)
        if not claims:
            logger.info(
                "no claims to answer from",
                extra={
                    "research_id": str(state["research_id"]),
                    "sources": len(state.get("sources") or ()),
                },
            )
            return NodeResult(value=None)

        context = AgentContext(
            research_id=state["research_id"],
            user_id=state["user_id"],
            mode=state["parameters"].mode,
            iteration=state.get("iteration", 0),
        )
        contradictions = contradiction_catalog(state)
        prompt = render(
            "answerer",
            question=state["query"],
            scope=_scope(state, claims),
            claim_count=str(len(claims)),
            claims=render_claims(claims),
            contradictions=_contradictions(contradictions, claims),
        )
        answer = await self.ask_stream(
            context,
            prompt=prompt,
            on_delta=on_delta,
            # The schema's own ceiling. A model that ignores its token limit
            # must not be able to write a draft the state cannot hold: the
            # failure would be a validation error after the reader had already
            # watched the whole thing arrive.
            max_chars=MAX_ANSWER_CHARS,
        )

        text = answer.value.strip()
        if not text:
            # A stream that delivered nothing. Not an exception: the provider
            # answered, it just answered with silence, and there is no draft to
            # store either way.
            logger.warning(
                "the answerer returned an empty answer",
                extra={"research_id": str(state["research_id"]), "model": answer.model},
            )
            return NodeResult(value=None, usage=answer.usage)

        draft = AnswerDraft(
            text=text,
            model=answer.model,
            claim_ids=_cited_claims(text, claims),
            truncated=len(text) >= MAX_ANSWER_CHARS,
        )
        logger.info(
            "answer written",
            extra={
                "research_id": str(state["research_id"]),
                "characters": len(draft.text),
                "words": draft.word_count,
                "citations": len(draft.claim_ids),
                "claims_offered": len(claims),
                "truncated": draft.truncated,
            },
        )
        return NodeResult(value=draft, usage=answer.usage)


def _cited_claims(markdown: str, claims: Catalog[ClaimItem]) -> tuple[UUID, ...]:
    """The claims the markers actually resolve to, in first-cited order.

    Markers that resolve to nothing stay in the text, exactly as they do in a
    report section. Scrubbing them would hide a fabricated citation rather than
    leave it visible where a reader - and the evaluation suite - can see it.
    """
    resolved: list[UUID] = []
    for marker in cited_numbers(markdown):
        claim = claims.get(marker)
        if claim is not None and claim.id not in resolved:
            resolved.append(claim.id)
    return tuple(resolved[:MAX_CLAIMS_PER_SECTION])


def _scope(state: ResearchState, claims: Catalog[ClaimItem]) -> str:
    """What the answer is being written from, in counts rather than adjectives.

    The same device the synthesizer is given, for the same reason: "drawn from
    four sources" and "drawn from forty" are different answers to the same
    question, and the writer is the only step that can say which this is.
    """
    all_claims = state.get("claims") or []
    unverified = sum(1 for claim in all_claims if claim.status is ClaimStatus.CANDIDATE)
    lines = [
        f"- Sources read: {len(state.get('sources') or ())}",
        f"- Claims available to cite: {len(claims)}"
        + (f", of which {unverified} are unverified candidates" if unverified else ""),
    ]
    if state.get("failed_tasks"):
        lines.append(
            f"- {len(state['failed_tasks'])} subtask(s) returned nothing, "
            "so parts of the question may be uncovered"
        )
    if (stop := state.get("stop")) is not None and stop.caveat:
        lines.append(
            "- Research stopped at a limit. A caveat saying so is shown beside "
            "this answer, added afterwards - do not write one yourself."
        )
    return "What this answer is being written from:\n" + "\n".join(lines)


def _contradictions(contradictions: Catalog[ContradictionItem], claims: Catalog[ClaimItem]) -> str:
    if not contradictions:
        return "No contradictions were detected between the claims."
    return (
        f"Claims that contradict each other ({len(contradictions)}). Give both sides "
        "and say they disagree; do not choose between them:\n"
        + render_contradictions(contradictions, claims=claims)
    )
