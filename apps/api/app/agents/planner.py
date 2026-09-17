"""The planner: a question into subtasks, once per round (FR-3, TDD 4.2).

Two things the agent decides rather than the model, because both are places
where a model's answer cannot be checked after the fact:

* **Subtask keys.** ``research_tasks.external_id`` has to be unique within a
  run, and the graph refuses a plan that names the same subtask twice. A
  model-chosen slug collides across rounds - "pricing" in round 1 and round 3 -
  and the collision surfaces as a lost subtask. Keys are derived from the round
  and the position instead.
* **The iteration.** The graph checks that the plan it gets belongs to the round
  it asked for and raises a contract violation otherwise. Letting the model
  supply it would turn an arithmetic slip into a failed run.

An empty plan is a legitimate answer, not a failure: a re-plan that finds
nothing worth researching ends discovery, and ``Plan`` allows it.
"""

from __future__ import annotations

from app.agents.base import AgentContext, ModelAgent
from app.agents.catalog import (
    Catalog,
    claim_catalog,
    render_claims,
    render_subtasks,
    subtask_catalog,
)
from app.agents.errors import AgentOutputUnusable
from app.agents.nodes import NodeResult
from app.agents.outputs import PlanOutput
from app.agents.prompting import render
from app.agents.schemas import (
    MAX_PLANNED_SUBTASKS,
    Critique,
    Plan,
    ResearchChannel,
    Subtask,
)
from app.agents.state import ResearchState
from app.core.enums import AgentName
from app.core.logging import get_logger
from app.models.gateway import LLMGateway

logger = get_logger(__name__)

#: A planner writes a short structured object. The ceiling is generous enough
#: for twenty subtasks with rationales and small enough that a model that starts
#: writing an essay is cut off rather than paid for.
MAX_PLAN_TOKENS = 4000


class PlannerAgent(ModelAgent):
    """Decomposes the question, and re-plans against the critic's gaps."""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        max_subtasks: int = MAX_PLANNED_SUBTASKS,
        dispatch_width: int,
        max_output_tokens: int = MAX_PLAN_TOKENS,
    ) -> None:
        super().__init__(gateway, role=AgentName.PLANNER, max_output_tokens=max_output_tokens)
        self._max_subtasks = min(max_subtasks, MAX_PLANNED_SUBTASKS)
        self._dispatch_width = dispatch_width

    async def plan(self, state: ResearchState) -> NodeResult[Plan]:
        iteration = state.get("iteration", 0) + 1
        context = AgentContext(
            research_id=state["research_id"],
            user_id=state["user_id"],
            mode=state["parameters"].mode,
            iteration=iteration,
        )
        subtasks = subtask_catalog(state)
        prompt = render(
            "planner",
            question=state["query"],
            parameters=_parameters(state),
            iteration=str(iteration),
            max_subtasks=str(self._max_subtasks),
            dispatch_width=str(self._dispatch_width),
            prior_work=_prior_work(state, subtasks),
        )

        output, usage = await self.ask(context, prompt=prompt, schema=PlanOutput)
        plan = Plan(
            research_goal=output.research_goal,
            iteration=iteration,
            subtasks=tuple(
                Subtask(
                    key=f"i{iteration}-{position}",
                    question=proposed.question,
                    priority=proposed.priority,
                    iteration=iteration,
                    rationale=proposed.rationale,
                    channel=_available(proposed.channel, state),
                )
                for position, proposed in enumerate(output.subtasks[: self._max_subtasks], start=1)
            ),
        )
        logger.info(
            "research plan produced",
            extra={
                "research_id": str(state["research_id"]),
                "iteration": iteration,
                "proposed": len(output.subtasks),
                "kept": len(plan.subtasks),
                "channels": sorted({task.channel.value for task in plan.subtasks}),
            },
        )
        return NodeResult(value=plan, usage=usage)


def _available(channel: ResearchChannel, state: ResearchState) -> ResearchChannel:
    """A channel the run can actually use.

    The router resolves this again at dispatch, against the corpus rather than
    the flag. Doing it here too keeps the *stored* plan honest: a subtask
    recorded as `documents` for a run with no documents would read, months
    later, as a subtask that was never run.
    """
    if channel is ResearchChannel.DOCUMENTS and not state["parameters"].has_attached_documents:
        return ResearchChannel.WEB
    return channel


def _parameters(state: ResearchState) -> str:
    """The run's settings, as trusted text. Every value here is the user's own."""
    parameters = state["parameters"]
    budget = state["budget"]
    lines = [
        f"- Mode: {parameters.mode.value}",
        f"- Depth: {parameters.depth} of 5",
        f"- Attached documents: {'yes' if parameters.has_attached_documents else 'none'}",
        f"- Research rounds allowed: {budget.max_iterations}",
        f"- Sources allowed: {budget.max_sources}; searches allowed: {budget.max_search_queries}",
    ]
    if parameters.domains:
        lines.append(f"- Restricted to these domains: {', '.join(parameters.domains)}")
    if parameters.date_range_start or parameters.date_range_end:
        start = parameters.date_range_start or "any"
        end = parameters.date_range_end or "any"
        lines.append(f"- Date range: {start} to {end}")
    if parameters.parent_research_id is not None:
        lines.append("- This is a follow-up to an earlier run.")
    return "\n".join(lines)


def _prior_work(state: ResearchState, subtasks: Catalog[Subtask]) -> str:
    """What earlier rounds found, and what the critic said is still missing.

    Empty on the first round, and the template says so - a first-round planner
    told "nothing has been researched yet" tends to explain that back to you.
    """
    if not subtasks:
        return "Nothing has been researched yet; this is the first round."

    critique = state.get("critique")
    parts = [
        f"Subtasks already researched ({len(subtasks)}):",
        render_subtasks(subtasks),
        "",
        _failures(state),
        "",
        _findings(state),
    ]
    if critique is not None:
        parts += ["", _gaps(critique, subtasks)]
    return "\n".join(part for part in parts if part)


def _failures(state: ResearchState) -> str:
    failed = state.get("failed_tasks") or []
    if not failed:
        return ""
    keys = sorted({failure.task_key for failure in failed if failure.task_key})
    return (
        f"{len(failed)} subtask attempt(s) failed and returned nothing"
        + (f" ({', '.join(keys)})." if keys else ".")
        + " Re-proposing one of those is reasonable if it still matters."
    )


def _findings(state: ResearchState) -> str:
    claims = claim_catalog(state)
    sources = len(state.get("sources") or ())
    if not claims:
        return f"{sources} source(s) were gathered but no claims came out of them yet."
    return f"{sources} source(s) gathered, {len(claims)} claim(s) so far:\n" + render_claims(claims)


def _gaps(critique: Critique, subtasks: Catalog[Subtask]) -> str:
    if critique.sufficient:
        return "The critic judged the previous round sufficient."
    items = []
    for gap in critique.missing:
        where = f" (subtask {gap.task_key})" if gap.task_key else ""
        items.append(f"- {gap.description}{where}")
    if not items:  # pragma: no cover - Critique refuses this combination
        raise AgentOutputUnusable("An insufficient critique named nothing missing.")
    known = {task.key for task in subtasks.items}
    unknown = sorted(
        {gap.task_key for gap in critique.missing if gap.task_key and gap.task_key not in known}
    )
    if unknown:
        logger.debug(
            "critic named a subtask the planner does not know",
            extra={"task_keys": unknown},
        )
    return "The critic judged the previous round insufficient. Still missing:\n" + "\n".join(items)
