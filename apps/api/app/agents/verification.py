"""Verification and contradiction detection (FR-6, FR-7, TDD 4.2).

Two nodes, one file, because they are two halves of the same judgement: what does
the evidence actually establish, and where does it disagree with itself. They
share a role in the routing table for the same reason.

**Verification re-scores; it never invents.** A verdict names a claim by
catalogue number and the agent re-emits *that* claim with a new status and
confidence, keeping its id and its evidence. The graph's reducer replaces by id,
so a re-scored claim takes its candidate's place rather than sitting beside it.
A claim the model does not return keeps the score it had - silence is not a
downgrade.

**Contradiction detection runs only where a contradiction could be.** Claims are
grouped by ``normalized_key``, and a key held by a single claim cannot disagree
with anything. A round whose claims are all about different things makes no model
call at all, which is both cheaper and more honest than asking a model to find
disagreement in a list with none in it.

Nothing here resolves a contradiction. That is FR-7 and it is a product
decision, not a modelling convenience: two sources that differ are a finding,
and a system that picks a winner silently is a system that reports one number
with the other one deleted.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from app.agents.base import AgentContext, ModelAgent
from app.agents.catalog import (
    Catalog,
    claim_catalog,
    evidence_catalog,
    render_claims,
    render_evidence,
    source_catalog,
)
from app.agents.nodes import NodeResult
from app.agents.outputs import (
    MAX_VERDICTS_PER_CALL,
    ContradictionOutput,
    VerificationOutput,
)
from app.agents.prompting import render
from app.agents.schemas import ClaimItem, ContradictionItem, NodeUsage
from app.agents.state import ResearchState
from app.core.enums import AgentName
from app.core.logging import get_logger
from app.models.gateway import LLMGateway

logger = get_logger(__name__)

MAX_VERIFICATION_TOKENS = 8000
MAX_CONTRADICTION_TOKENS = 4000

#: Namespace for derived contradiction ids. Fixed forever, like the claim one.
_CONTRADICTION_NAMESPACE = uuid.UUID("b2c8f0d4-3e51-4a6b-8c97-1d0a5e2f9b47")


class VerificationAgent(ModelAgent):
    """Decides what the evidence establishes, and how firmly."""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        max_output_tokens: int = MAX_VERIFICATION_TOKENS,
    ) -> None:
        super().__init__(gateway, role=AgentName.VERIFIER, max_output_tokens=max_output_tokens)

    async def verify(self, state: ResearchState) -> NodeResult[tuple[ClaimItem, ...]]:
        context = _context(state)
        # Capped at what one call may answer, not at what a prompt may carry: a
        # claim shown but not answerable would be read and then silently
        # unscoreable, which looks from the outside like the model ignoring it.
        claims = claim_catalog(state, limit=MAX_VERDICTS_PER_CALL)
        if not claims:
            return NodeResult(value=(), usage=NodeUsage())

        evidence = evidence_catalog(state)
        sources = source_catalog(state)
        prompt = render(
            "verifier",
            question=state["query"],
            claim_count=str(len(claims)),
            claims=render_claims(claims, evidence=evidence, sources=sources),
            evidence_count=str(len(evidence)),
            evidence=render_evidence(evidence, sources=sources),
        )
        output, usage = await self.ask(context, prompt=prompt, schema=VerificationOutput)

        scored: dict[uuid.UUID, ClaimItem] = {}
        invented = 0
        for verdict in output.verdicts:
            claim = claims.get(verdict.claim)
            if claim is None:
                invented += 1
                continue
            scored[claim.id] = claim.model_copy(
                update={"status": verdict.status, "confidence": verdict.confidence}
            )

        logger.info(
            "claims verified",
            extra={
                "research_id": str(state["research_id"]),
                "iteration": context.iteration,
                "claims": len(claims),
                "scored": len(scored),
                "unscored": len(claims) - len(scored),
                "named_unknown_claims": invented,
                "statuses": sorted({claim.status.value for claim in scored.values()}),
            },
        )
        return NodeResult(value=tuple(scored.values()), usage=usage)


class ContradictionAgent(ModelAgent):
    """Finds disagreements between claims. Records them; never settles them."""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        max_output_tokens: int = MAX_CONTRADICTION_TOKENS,
    ) -> None:
        # The verifier's role: contradiction checking is the second half of
        # verification and is routed to the same tier (``GraphNode.agent``).
        super().__init__(gateway, role=AgentName.VERIFIER, max_output_tokens=max_output_tokens)

    async def find_contradictions(
        self, state: ResearchState
    ) -> NodeResult[tuple[ContradictionItem, ...]]:
        context = _context(state)
        contested = _contested(claim_catalog(state))
        if not contested:
            logger.debug(
                "no claim shares a key with another, so nothing can contradict",
                extra={
                    "research_id": str(state["research_id"]),
                    "iteration": context.iteration,
                },
            )
            return NodeResult(value=(), usage=NodeUsage())

        evidence = evidence_catalog(state)
        sources = source_catalog(state)
        prompt = render(
            "contradictions",
            question=state["query"],
            claim_count=str(len(contested)),
            claims=render_claims(contested, evidence=evidence, sources=sources),
        )
        output, usage = await self.ask(context, prompt=prompt, schema=ContradictionOutput)

        found: dict[uuid.UUID, ContradictionItem] = {}
        rejected = 0
        for pair in output.contradictions:
            first = contested.get(pair.claim_a)
            second = contested.get(pair.claim_b)
            if first is None or second is None or first.id == second.id:
                rejected += 1
                continue
            if first.normalized_key != second.normalized_key:
                # Two claims about different things are two facts, not a
                # disagreement. Refused here rather than left to the report,
                # where it would read as a conflict the sources never had.
                rejected += 1
                continue
            item = _pair(state["research_id"], first, second, reason=pair.likely_reason)
            found[item.id] = item

        logger.info(
            "contradiction check finished",
            extra={
                "research_id": str(state["research_id"]),
                "iteration": context.iteration,
                "claims_sharing_a_key": len(contested),
                "reported": len(output.contradictions),
                "kept": len(found),
                "rejected": rejected,
            },
        )
        return NodeResult(value=tuple(found.values()), usage=usage)


def _contested(claims: Catalog[ClaimItem]) -> Catalog[ClaimItem]:
    """Claims whose key is shared by at least one other claim.

    The catalogue is renumbered over this subset, so the numbers the model is
    given are the numbers it answers with. Renumbering is safe because nothing
    else has seen these numbers - unlike the synthesizer's catalogue, which the
    citation validator has to rebuild identically.
    """
    by_key: dict[str, list[ClaimItem]] = defaultdict(list)
    for claim in claims.items:
        by_key[claim.normalized_key].append(claim)
    return Catalog(tuple(claim for claim in claims.items if len(by_key[claim.normalized_key]) > 1))


def _pair(
    research_id: uuid.UUID, first: ClaimItem, second: ClaimItem, *, reason: str
) -> ContradictionItem:
    """A contradiction with a derived id, so finding it twice records it once.

    The claims are ordered by id before the id is derived: "A contradicts B" and
    "B contradicts A" are one disagreement, and a run that reported both would
    show the reader the same conflict twice.
    """
    left, right = sorted((first, second), key=lambda claim: str(claim.id))
    return ContradictionItem(
        id=uuid.uuid5(_CONTRADICTION_NAMESPACE, f"{research_id}:{left.id}:{right.id}"),
        normalized_key=left.normalized_key,
        claim_a_id=left.id,
        claim_b_id=right.id,
        likely_reason=reason,
    )


def _context(state: ResearchState) -> AgentContext:
    return AgentContext(
        research_id=state["research_id"],
        user_id=state["user_id"],
        mode=state["parameters"].mode,
        iteration=state.get("iteration", 0),
    )
