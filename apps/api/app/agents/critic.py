"""The critic: is what has been gathered enough, or is another round worth it (FR-8).

The only node whose answer spends money. A verdict of "insufficient" sends the
graph back to the planner and buys another round of searches, fetches, model
calls and time; a verdict of "sufficient" ends discovery and writes the report.
So the critic is given the numbers it is deciding with - which round this is, how
many are allowed, what was found, what failed - and the prompt is explicit that
more is not automatically better.

Two things the agent enforces around the model:

* **An insufficient verdict must be actionable.** ``Critique`` refuses one that
  names nothing missing, because the next planning round would then have nothing
  to research and the loop would spin. A verdict that says "insufficient" with an
  empty list is corrected to sufficient here, with the correction logged, rather
  than raised: a critic that cannot say what is missing has, in effect, not
  found anything missing.
* **The iteration is the agent's.** The graph checks that a critique belongs to
  the round it judged and treats a mismatch as a broken contract.
"""

from __future__ import annotations

from app.agents.base import AgentContext, ModelAgent
from app.agents.catalog import (
    Catalog,
    claim_catalog,
    contradiction_catalog,
    render_claims,
    render_contradictions,
    render_subtasks,
    subtask_catalog,
)
from app.agents.nodes import NodeResult
from app.agents.outputs import CritiqueOutput
from app.agents.prompting import render
from app.agents.schemas import ClaimItem, ContradictionItem, Critique, MissingInfo, Subtask
from app.agents.state import ResearchState
from app.core.enums import AgentName, ClaimStatus
from app.core.logging import get_logger
from app.models.gateway import LLMGateway

logger = get_logger(__name__)

MAX_CRITIC_TOKENS = 4000


class CriticAgent(ModelAgent):
    """Decides whether to stop researching, and says what is missing if not."""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        max_output_tokens: int = MAX_CRITIC_TOKENS,
    ) -> None:
        super().__init__(gateway, role=AgentName.CRITIC, max_output_tokens=max_output_tokens)

    async def critique(self, state: ResearchState) -> NodeResult[Critique]:
        iteration = state.get("iteration", 0)
        context = AgentContext(
            research_id=state["research_id"],
            user_id=state["user_id"],
            mode=state["parameters"].mode,
            iteration=iteration,
        )
        subtasks = subtask_catalog(state)
        claims = claim_catalog(state)
        contradictions = contradiction_catalog(state)
        prompt = render(
            "critic",
            question=state["query"],
            iteration=str(iteration),
            max_iterations=str(state["budget"].max_iterations),
            subtask_count=str(len(subtasks)),
            subtasks=render_subtasks(subtasks) or "None yet.",
            coverage=_coverage(state),
            claim_count=str(len(claims)),
            claims=render_claims(claims) or "No claims have been made.",
            contradictions=_contradictions(contradictions, claims),
        )
        output, usage = await self.ask(context, prompt=prompt, schema=CritiqueOutput)

        missing = tuple(
            MissingInfo(
                description=item.description,
                task_key=_task_key(item.subtask, subtasks),
            )
            for item in output.missing
        )
        sufficient = output.sufficient or not missing
        if not output.sufficient and not missing:
            logger.warning(
                "the critic said insufficient without saying what is missing",
                extra={
                    "research_id": str(state["research_id"]),
                    "iteration": iteration,
                },
            )
        logger.info(
            "coverage judged",
            extra={
                "research_id": str(state["research_id"]),
                "iteration": iteration,
                "sufficient": sufficient,
                "missing": len(missing),
                "claims": len(claims),
                "contradictions": len(contradictions),
            },
        )
        return NodeResult(
            value=Critique(
                iteration=iteration,
                sufficient=sufficient,
                missing=() if sufficient else missing,
                rationale=output.rationale,
            ),
            usage=usage,
        )


def _coverage(state: ResearchState) -> str:
    """What the round actually produced, in counts the critic can weigh.

    Counts rather than prose, and including the failures: a subtask that failed
    outright is the strongest reason to run another round, and it is invisible
    in a list of claims.
    """
    all_claims = state.get("claims") or []
    by_status = {
        status: sum(1 for claim in all_claims if claim.status is status) for status in ClaimStatus
    }
    failed = state.get("failed_tasks") or []
    errors = state.get("errors") or []
    lines = [
        f"- Sources gathered: {len(state.get('sources') or ())}",
        f"- Evidence spans: {len(state.get('evidence') or ())}",
        "- Claims by status: "
        + ", ".join(f"{status.value} {count}" for status, count in by_status.items()),
    ]
    if failed:
        keys = sorted({failure.task_key for failure in failed if failure.task_key})
        lines.append(
            f"- {len(failed)} subtask attempt(s) returned nothing"
            + (f": {', '.join(keys)}" if keys else "")
        )
    if errors:
        lines.append(f"- {len(errors)} processing step(s) failed during this run")
    return "What this run has so far:\n" + "\n".join(lines)


def _contradictions(contradictions: Catalog[ContradictionItem], claims: Catalog[ClaimItem]) -> str:
    """Contradictions as context, labelled as findings rather than as gaps.

    Said explicitly because it is the critic's most tempting mistake: a
    disagreement between sources looks like missing information, and researching
    it again usually just finds both sides a second time.
    """
    if not contradictions:
        return "No contradictions have been detected."
    return (
        f"Contradictions detected ({len(contradictions)}). They are findings to report, "
        "not gaps to research away:\n" + render_contradictions(contradictions, claims=claims)
    )


def _task_key(number: int | None, subtasks: Catalog[Subtask]) -> str | None:
    """The subtask a gap belongs to, or ``None`` when the model named no real one."""
    if number is None:
        return None
    subtask = subtasks.get(number)
    return subtask.key if subtask is not None else None
