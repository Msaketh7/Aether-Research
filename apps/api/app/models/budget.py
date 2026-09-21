"""Per-run spend, enforced before the money is spent (Phase 16, FR-8).

The graph already stops a run whose estimated cost has reached its ceiling, at
the boundary between two nodes (``app.agents.budget``). That is the right place
for the decision and the wrong place for the *bound*: a node that starts under
the ceiling may finish well over it, because a step is never interrupted
mid-call. A researcher fanned out four ways can therefore overshoot by four
model calls before anything notices.

This closes that gap at the only other place every call passes through. Two
refusals, and each one exists because the alternative is a ceiling that is not
a ceiling:

* **A run that has already spent its allowance makes no further calls.** Not
  "no further rounds" - no further calls. The node fails with a bounded,
  non-retryable error, the graph records it as a stop rather than a failure,
  and the run proceeds to synthesis with a caveat naming the limit (FR-8).
* **A model with no declared price is refused outright** when a run is under a
  budget. ``app.agents.budget`` has been saying since Phase 9 that an uncosted
  call makes the ceiling unenforceable and ends discovery at the end of its
  round; refusing the call is the version of that which costs nothing, and it
  is what that module's note pointed at.

**Spend is counted from the ledger, not estimated separately.** The guard is a
``CallRecorder`` that wraps the real one, so the number it enforces against is
built from exactly the records that are written - a total that could drift from
the ledger would be a second, weaker truth about what a run cost.

**A run that is not enrolled is not refused.** Calls outside a run - an
evaluation judge, a one-off script - have no per-run ceiling, and inventing one
here would make the gateway unusable for them. The enrolment is explicit
(``for_run``) and belongs to whoever owns the run, which is the worker.

**The steps that write are never refused, and that is the whole point.** FR-8
says a run that hits a limit stops safely and returns a *partial result*; the
partial result is the answer and the report, and each costs a model call. A
guard that refused them would turn every budget stop into a run with nothing to
show - the exact outcome the requirement exists to prevent. So the answerer and
the synthesizer are exempt, the overshoot is two calls wide, and the report says
in its own caveat that discovery stopped at a limit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.core.enums import AgentName
from app.core.logging import get_logger
from app.models.errors import ModelError
from app.models.recording import CallRecorder, LlmCallRecord
from app.models.registry import ModelSpec

logger = get_logger(__name__)

#: Roles whose calls finish a run rather than extend it. Always authorised -
#: see the module docstring. The answerer is here for the same reason the
#: synthesizer is, and with more force: it writes the paragraph the reader
#: actually reads, and a run stopped by its ceiling that shows nothing at all is
#: the failure FR-8 names.
FINISHING_ROLES: frozenset[AgentName] = frozenset({AgentName.ANSWERER, AgentName.SYNTHESIZER})


class BudgetExhausted(ModelError):
    """This run may not spend any more.

    Not retryable and not a failover: every model in the chain costs money, and
    trying a cheaper one would be this class deciding what a run should buy.
    """

    status_code = 402
    code = "budget_exhausted"
    message = "This research run has reached the amount it was allowed to spend."
    retryable = False
    failover = False


class ModelNotPriced(ModelError):
    """The routed model has no declared price, and this run has a ceiling.

    Refused rather than allowed-and-unmeasured: a run whose spend cannot be
    measured cannot be held to a limit, and the product's promise is the limit.
    """

    status_code = 402
    code = "model_not_priced"
    message = "The model routed to this step has no declared price."
    retryable = False
    failover = True


class BudgetGuard(Protocol):
    """What the gateway asks before it spends anything."""

    def authorise(
        self,
        *,
        run_id: UUID | None,
        spec: ModelSpec,
        operation: str,
        role: AgentName | None = None,
    ) -> None:
        """Raise when this call must not be made. Returns nothing when it may."""
        ...


@dataclass(frozen=True, slots=True)
class RunSpend:
    """What one run has spent, as the ledger records it."""

    limit_usd: float
    spent_usd: float = 0.0
    calls: int = 0
    #: Calls whose price the registry does not declare. Never counted as zero.
    uncosted_calls: int = 0

    @property
    def exhausted(self) -> bool:
        return self.spent_usd >= self.limit_usd

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.limit_usd - self.spent_usd)


class NullBudgetGuard:
    """Authorises everything. What a process with no run budgets uses."""

    def authorise(
        self,
        *,
        run_id: UUID | None,
        spec: ModelSpec,
        operation: str,
        role: AgentName | None = None,
    ) -> None:
        return None


class RunBudgetGuard:
    """Tracks what each enrolled run has spent, and refuses the call after.

    One per process, like the gateway it guards, because a worker holds several
    runs at once and a guard per run would be a guard per nobody.
    """

    def __init__(self, recorder: CallRecorder, *, require_priced: bool = True) -> None:
        self._recorder = recorder
        self._require_priced = require_priced
        self._runs: dict[UUID, RunSpend] = {}

    # --- enrolment --------------------------------------------------------

    @asynccontextmanager
    async def for_run(
        self, run_id: UUID, *, limit_usd: float, spent_usd: float = 0.0
    ) -> AsyncIterator[RunSpend]:
        """Hold a run to ``limit_usd`` for as long as this context is open.

        ``spent_usd`` is what the run had already spent before this attempt,
        and the caller supplies it because the caller is the one holding the
        run's row. The budget belongs to the *run*, not to the attempt: a run
        that crashes twice must not get three budgets, and the guard's own
        memory cannot know that - a resumed run is usually a different
        process, and this one has never seen it.
        """
        self._runs[run_id] = RunSpend(limit_usd=limit_usd, spent_usd=spent_usd)
        try:
            yield self._runs[run_id]
        finally:
            spend = self._runs.pop(run_id, None)
            if spend is not None:
                logger.info(
                    "run budget released",
                    extra={
                        "run_id": str(run_id),
                        "spent_usd": round(spend.spent_usd, 6),
                        "limit_usd": spend.limit_usd,
                        "calls": spend.calls,
                        "uncosted_calls": spend.uncosted_calls,
                    },
                )

    def spend_of(self, run_id: UUID) -> RunSpend | None:
        """What this run has spent so far, or ``None`` if it is not enrolled."""
        return self._runs.get(run_id)

    @property
    def enrolled(self) -> Mapping[UUID, RunSpend]:
        return dict(self._runs)

    # --- the guard --------------------------------------------------------

    def authorise(
        self,
        *,
        run_id: UUID | None,
        spec: ModelSpec,
        operation: str,
        role: AgentName | None = None,
    ) -> None:
        if run_id is None or role in FINISHING_ROLES:
            return
        spend = self._runs.get(run_id)
        if spend is None:
            return
        if spend.exhausted:
            raise BudgetExhausted(
                "This research run has reached the amount it was allowed to spend.",
                context={
                    "run_id": str(run_id),
                    "spent_usd": round(spend.spent_usd, 6),
                    "limit_usd": spend.limit_usd,
                    "operation": operation,
                },
            )
        if self._require_priced and not spec.is_priced:
            raise ModelNotPriced(
                "The model routed to this step has no declared price, so this run's "
                "spending limit could not be enforced against it.",
                context={"run_id": str(run_id), "model": spec.key, "operation": operation},
            )

    # --- the ledger -------------------------------------------------------

    async def record(self, call: LlmCallRecord) -> None:
        """Count the call, then pass it on to the real recorder.

        Every attempt counts, the failed ones included: a retry that burned
        prompt tokens and returned an error was still paid for.
        """
        if call.run_id is not None:
            spend = self._runs.get(call.run_id)
            if spend is not None:
                self._runs[call.run_id] = RunSpend(
                    limit_usd=spend.limit_usd,
                    spent_usd=spend.spent_usd + (call.cost_usd or 0.0),
                    calls=spend.calls + 1,
                    uncosted_calls=spend.uncosted_calls + (1 if call.cost_usd is None else 0),
                )
        await self._recorder.record(call)
