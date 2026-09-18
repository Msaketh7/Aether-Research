"""Per-run spend, and the calls it refuses (Phase 16, FR-8).

The graph's own ceiling check is tested in ``test_graph_budget.py``; this is
the other half, at the gateway, where a ceiling can actually stop a call
rather than notice afterwards that one was made. What matters here is exactly
which calls are refused, which are not, and that the number being enforced
against comes from the ledger rather than from a second tally.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.core.enums import AgentName, LlmCallStatus, LlmProvider, ResearchMode
from app.models import (
    BudgetExhausted,
    CollectingCallRecorder,
    LLMGateway,
    ModelNotPriced,
    ModelRegistry,
    ModelRouter,
    ModelSpec,
    ModelTier,
    RunBudgetGuard,
)
from app.models.recording import LlmCallRecord
from app.models.registry import Pricing
from tests.test_model_gateway import PROMPT, ScriptedProvider, spec


def record(
    *,
    run_id: uuid.UUID | None,
    cost: float | None = 0.25,
    role: AgentName = AgentName.RESEARCHER,
) -> LlmCallRecord:
    return LlmCallRecord(
        role=role,
        mode=ResearchMode.DEEP,
        provider=LlmProvider.ANTHROPIC,
        model="anthropic:sonnet",
        operation="generate",
        status=LlmCallStatus.OK,
        prompt_tokens=100,
        completion_tokens=50,
        cost_usd=cost,
        latency_ms=12,
        prompt_version="v1",
        attempt=1,
        run_id=run_id,
    )


# --- what the guard counts ------------------------------------------------


async def test_spend_is_counted_from_the_records_that_are_written():
    """One number, not two: a total that could drift from the ledger would be
    a second, weaker truth about what a run cost."""
    sink = CollectingCallRecorder()
    guard = RunBudgetGuard(sink)
    run_id = uuid.uuid4()

    async with guard.for_run(run_id, limit_usd=1.0):
        await guard.record(record(run_id=run_id, cost=0.25))
        await guard.record(record(run_id=run_id, cost=0.25))
        spend = guard.spend_of(run_id)

    assert spend is not None
    assert (spend.spent_usd, spend.calls) == (0.5, 2)
    assert len(sink.calls) == 2, "and the real recorder still saw every one"


async def test_a_failed_attempt_is_still_money_spent():
    """A retry that burned prompt tokens and returned an error was paid for."""
    guard = RunBudgetGuard(CollectingCallRecorder())
    run_id = uuid.uuid4()

    async with guard.for_run(run_id, limit_usd=1.0):
        await guard.record(record(run_id=run_id, cost=0.3))
        await guard.record(record(run_id=run_id, cost=0.3))
        spend = guard.spend_of(run_id)

    assert spend is not None and spend.spent_usd == pytest.approx(0.6)


async def test_an_uncosted_call_is_counted_as_uncosted_not_as_free():
    guard = RunBudgetGuard(CollectingCallRecorder())
    run_id = uuid.uuid4()

    async with guard.for_run(run_id, limit_usd=1.0):
        await guard.record(record(run_id=run_id, cost=None))
        spend = guard.spend_of(run_id)

    assert spend is not None
    assert (spend.spent_usd, spend.uncosted_calls) == (0.0, 1)


async def test_a_resumed_run_starts_from_what_it_already_spent():
    """The budget belongs to the run, not to the attempt.

    The guard cannot know this by itself - a resumed run is usually a
    different process, which has never seen the first attempt - so the caller
    supplies the starting point from the row it claimed. Written first without
    that, which gave a run that crashed twice three budgets.
    """
    guard = RunBudgetGuard(CollectingCallRecorder())
    run_id = uuid.uuid4()

    async with guard.for_run(run_id, limit_usd=1.0, spent_usd=0.8) as resumed:
        assert resumed.spent_usd == pytest.approx(0.8)
        await guard.record(record(run_id=run_id, cost=0.4))
        with pytest.raises(BudgetExhausted):
            guard.authorise(run_id=run_id, spec=spec("m"), operation="generate")


async def test_a_run_is_forgotten_once_it_ends():
    guard = RunBudgetGuard(CollectingCallRecorder())
    run_id = uuid.uuid4()

    async with guard.for_run(run_id, limit_usd=1.0):
        pass

    assert guard.spend_of(run_id) is None
    assert guard.enrolled == {}


# --- what the guard refuses -----------------------------------------------


async def test_a_run_that_has_spent_its_allowance_makes_no_further_calls():
    guard = RunBudgetGuard(CollectingCallRecorder())
    run_id = uuid.uuid4()

    async with guard.for_run(run_id, limit_usd=0.5):
        guard.authorise(run_id=run_id, spec=spec("m"), operation="generate")
        await guard.record(record(run_id=run_id, cost=0.5))

        with pytest.raises(BudgetExhausted) as raised:
            guard.authorise(run_id=run_id, spec=spec("m"), operation="generate")

    assert raised.value.retryable is False
    assert raised.value.failover is False, "every model in the chain costs money"


async def test_the_step_that_writes_the_report_is_never_refused():
    """FR-8 says a run that hits a limit returns a partial result, and the
    partial result is a report - which costs a call."""
    guard = RunBudgetGuard(CollectingCallRecorder())
    run_id = uuid.uuid4()

    async with guard.for_run(run_id, limit_usd=0.1):
        await guard.record(record(run_id=run_id, cost=5.0))

        guard.authorise(
            run_id=run_id, spec=spec("m"), operation="generate", role=AgentName.SYNTHESIZER
        )
        with pytest.raises(BudgetExhausted):
            guard.authorise(
                run_id=run_id, spec=spec("m"), operation="generate", role=AgentName.PLANNER
            )


async def test_an_unpriced_model_is_refused_under_a_budget():
    """A run whose spend cannot be measured cannot be held to a limit."""
    guard = RunBudgetGuard(CollectingCallRecorder())
    run_id = uuid.uuid4()

    async with guard.for_run(run_id, limit_usd=1.0):
        with pytest.raises(ModelNotPriced) as raised:
            guard.authorise(run_id=run_id, spec=spec("free", priced=False), operation="generate")

    assert raised.value.failover is True, "the next model in the chain may be priced"


async def test_unpriced_models_can_be_allowed_on_purpose():
    guard = RunBudgetGuard(CollectingCallRecorder(), require_priced=False)
    run_id = uuid.uuid4()

    async with guard.for_run(run_id, limit_usd=1.0):
        guard.authorise(run_id=run_id, spec=spec("free", priced=False), operation="generate")


async def test_a_call_outside_any_run_is_never_refused():
    """An evaluation judge has no per-run ceiling, and inventing one here
    would make the gateway unusable for it."""
    guard = RunBudgetGuard(CollectingCallRecorder())

    guard.authorise(run_id=None, spec=spec("free", priced=False), operation="generate")
    guard.authorise(run_id=uuid.uuid4(), spec=spec("m"), operation="generate")


# --- through the gateway --------------------------------------------------


def gateway_with(guard: RunBudgetGuard, *specs: ModelSpec) -> LLMGateway:
    registry = ModelRegistry(specs={s.key: s for s in specs})
    return LLMGateway(
        registry=registry,
        router=ModelRouter(registry),
        providers={LlmProvider.ANTHROPIC: ScriptedProvider()},  # type: ignore[dict-item]
        recorder=guard,
        budget=guard,
        retry_base_delay_seconds=0.001,
        retry_max_delay_seconds=0.002,
    )


async def test_the_gateway_stops_spending_when_the_run_has_none_left():
    sink = CollectingCallRecorder()
    guard = RunBudgetGuard(sink)
    # Priced high on purpose: one call of 100 prompt and 50 completion tokens
    # costs more than the ceiling, so the second call is the refused one.
    expensive = ModelSpec(
        key="strong",
        provider=LlmProvider.ANTHROPIC,
        model_id="strong",
        tier=ModelTier.STRONG,
        context_window=100_000,
        max_output_tokens=4096,
        pricing=Pricing(1000.0, 2000.0, dt.date(2026, 1, 1), "expensive on purpose"),
    )
    run_id = uuid.uuid4()
    gateway = gateway_with(guard, expensive)

    async with guard.for_run(run_id, limit_usd=0.15):
        first = await gateway.generate(
            role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT, run_id=run_id
        )
        with pytest.raises(BudgetExhausted):
            await gateway.generate(
                role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT, run_id=run_id
            )

    assert first.text == "ok"
    # The refusal is recorded: a run that hit its limit should be able to show
    # where, rather than simply having a call missing from its ledger.
    assert [call.status for call in sink.calls] == [LlmCallStatus.OK, LlmCallStatus.ERROR]
    assert sink.calls[-1].error_code == "budget_exhausted"
