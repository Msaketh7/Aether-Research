"""What every model-backed agent shares: one call, priced and logged.

**Where the bounds already are, and why this adds none.** A model call is
bounded three times before it reaches here: the gateway gives it a per-request
timeout, bounded retries and a declared failover chain (ADR 0007); the toolbelt
does the same for tool calls; and the graph wraps every node in a timeout it
cannot exceed, a cancel check and the FR-8 ceilings (Phase 9). A fourth timeout
inside each agent would not make a run safer - it would be a fourth number to
keep consistent with the other three, and the first one anybody forgot to update.
What agents do own is how many calls they make, which is bounded per agent and
from the run's allowances.

**Usage is reported, not estimated.** ``NodeUsage`` comes from the completion
the provider returned and the price the registry declares. When the model is
unpriced the agent reports an uncosted call rather than zero, and the governor
ends discovery at the end of that round because a ceiling it cannot measure is
not a ceiling (``app.agents.budget``).

**Observability** is a structured line per agent step - role, run, latency,
tokens, cost, and whether the cost is known - which is what makes a run's
spending answerable from logs before Phase 17's trace tables exist.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import UUID

from app.agents.schemas import CostEstimate, NodeUsage, TokenCount
from app.core.enums import AgentName, AgentStatus, ResearchMode
from app.core.logging import get_logger
from app.models.base import Completion, Prompt, StructuredT
from app.models.gateway import LLMGateway

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AgentContext:
    """Who is asking, on whose behalf, and in which round.

    Passed to every call so that a log line and a ledger row can be joined back
    to the run without an agent having to thread a run id through by hand.
    """

    research_id: UUID
    user_id: UUID
    mode: ResearchMode
    iteration: int = 0
    task_key: str | None = None


def usage_of(gateway: LLMGateway, completion: Completion, *, searches: int = 0) -> NodeUsage:
    """What one completion consumed, priced through the gateway.

    ``cost_of`` returns ``None`` for a model with no declared price. That is
    recorded as an uncosted call - never as ``0.0`` - so the run's total stays
    honest about what it could not measure.
    """
    price = gateway.cost_of(completion)
    return NodeUsage(
        tokens=TokenCount(
            prompt_tokens=completion.usage.prompt_tokens,
            completion_tokens=completion.usage.completion_tokens,
        ),
        cost=(CostEstimate(usd=price) if price is not None else CostEstimate(uncosted_calls=1)),
        search_queries=searches,
    )


def with_searches(usage: NodeUsage, searches: int) -> NodeUsage:
    """The same usage, with web searches added to it.

    Separate from ``usage_of`` because a search is a tool call rather than a
    model call: the count comes from what the researcher issued, not from any
    completion.
    """
    return usage.model_copy(update={"search_queries": usage.search_queries + searches})


def total_usage(parts: list[NodeUsage]) -> NodeUsage:
    """Sum the usage of several calls made inside one node."""
    tokens = TokenCount()
    cost = CostEstimate()
    searches = 0
    for part in parts:
        tokens = tokens + part.tokens
        cost = cost + part.cost
        searches += part.search_queries
    return NodeUsage(tokens=tokens, cost=cost, search_queries=searches)


class ModelAgent:
    """An agent that reaches a model only through the gateway, by role.

    The role is fixed per agent, which is what makes model choice a routing
    policy rather than a decision inside a node (ADR 0007): changing which model
    plans a run is a line in ``routing.py``, not an edit here.
    """

    #: The role this agent's calls are routed and attributed to.
    role: AgentName

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        role: AgentName,
        max_output_tokens: int,
        temperature: float | None = None,
    ) -> None:
        self._gateway = gateway
        self.role = role
        self._max_output_tokens = max_output_tokens
        self._temperature = temperature

    async def ask(
        self,
        context: AgentContext,
        *,
        prompt: Prompt,
        schema: type[StructuredT],
    ) -> tuple[StructuredT, NodeUsage]:
        """One structured call: the validated value, and what it cost.

        Failures propagate. The gateway has already retried what retrying can
        fix and failed over what it cannot, so an exception here means every
        declared model refused or the answer would not validate - and the node
        boundary above decides what that costs the run.
        """
        started = time.perf_counter()
        logger.debug(
            "agent step started",
            extra={**self._log_fields(context), "status": AgentStatus.RUNNING.value},
        )
        try:
            result = await self._gateway.generate_structured(
                role=self.role,
                mode=context.mode,
                prompt=prompt,
                schema=schema,
                max_output_tokens=self._max_output_tokens,
                temperature=self._temperature,
                run_id=context.research_id,
            )
        except Exception as exc:
            logger.warning(
                "agent step failed",
                extra={
                    **self._log_fields(context),
                    "status": AgentStatus.ERROR.value,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "prompt_version": prompt.version,
                    "error_type": type(exc).__name__,
                },
            )
            raise

        usage = usage_of(self._gateway, result.completion)
        logger.info(
            "agent step finished",
            extra={
                **self._log_fields(context),
                "status": AgentStatus.OK.value,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "prompt_version": prompt.version,
                "model": result.completion.model,
                "prompt_tokens": usage.tokens.prompt_tokens,
                "completion_tokens": usage.tokens.completion_tokens,
                "cost_usd": usage.cost.usd if usage.cost.measured else None,
                "cost_known": usage.cost.measured,
            },
        )
        return result.value, usage

    def _log_fields(self, context: AgentContext) -> dict[str, object]:
        return {
            "agent": self.role.value,
            "research_id": str(context.research_id),
            "iteration": context.iteration,
            "task_key": context.task_key,
        }
