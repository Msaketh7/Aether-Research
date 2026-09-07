"""Recording every model call.

The project rule is unconditional: *record every LLM call - tokens, cost,
latency, status*. This is where that happens, once, for every provider and every
operation, because a rule enforced at each call site is a rule that will be
missed at the next call site someone adds.

A record is produced for **every attempt**, not every successful call. A run that
burned three retries and a failover before succeeding cost four calls, and a
ledger that shows one is not a ledger. `status` follows the `llm_calls` vocabulary
in TDD 7.2: `ok`, `error`, `fallback`.

The sink is an interface with a logging implementation. Writing to the
`llm_calls` table needs an `agent_run` to hang the row from, which does not exist
until the graph does (Phases 9-10), and inventing one now would mean writing rows
that describe nothing. Structured logs carry the same fields in the meantime, so
nothing is lost and Phase 17 swaps the implementation without touching a caller.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from uuid import UUID

from app.core.enums import AgentName, LlmCallStatus, LlmProvider, ResearchMode
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class LlmCallRecord:
    """One attempt at one model call.

    Mirrors the `llm_calls` columns so Phase 17's database sink is a mapping,
    not a redesign.
    """

    role: AgentName
    mode: ResearchMode
    provider: LlmProvider
    model: str
    operation: str
    status: LlmCallStatus
    prompt_tokens: int
    completion_tokens: int
    #: ``None`` means the model has no declared price - *not measured*, which is
    #: a different fact from a call that cost nothing. Phase 16 acts on this.
    cost_usd: float | None
    latency_ms: int
    prompt_version: str
    attempt: int
    #: Set when this attempt followed a failure on a different model.
    fell_back_from: str | None = None
    error_code: str | None = None
    run_id: UUID | None = None
    request_id: str | None = None
    #: default_factory, not a call in the default: evaluated once at import
    #: time, every record in the process would share one timestamp.
    occurred_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@runtime_checkable
class CallRecorder(Protocol):
    """Where call records go."""

    async def record(self, call: LlmCallRecord) -> None: ...


class LoggingCallRecorder:
    """Writes one structured log line per attempt.

    Enough to answer "what did this run cost and where did the time go" from
    logs alone, which is what the system needs before the trace tables are
    being written.
    """

    async def record(self, call: LlmCallRecord) -> None:
        logger.info(
            "llm call",
            extra={
                "role": call.role.value,
                "mode": call.mode.value,
                "provider": call.provider.value,
                "model": call.model,
                "operation": call.operation,
                "status": call.status.value,
                "prompt_tokens": call.prompt_tokens,
                "completion_tokens": call.completion_tokens,
                "total_tokens": call.total_tokens,
                # Explicitly nullable in the log too: a reader must be able to
                # tell an uncosted call from a free one.
                "cost_usd": call.cost_usd,
                "cost_known": call.cost_usd is not None,
                "latency_ms": call.latency_ms,
                "prompt_version": call.prompt_version,
                "attempt": call.attempt,
                "fell_back_from": call.fell_back_from,
                "error_code": call.error_code,
                "run_id": str(call.run_id) if call.run_id else None,
                "request_id": call.request_id,
            },
        )


class CollectingCallRecorder:
    """Keeps records in memory. For tests and for a single-run cost summary."""

    def __init__(self) -> None:
        self.calls: list[LlmCallRecord] = []

    async def record(self, call: LlmCallRecord) -> None:
        self.calls.append(call)

    @property
    def total_tokens(self) -> int:
        return sum(call.total_tokens for call in self.calls)

    @property
    def total_cost_usd(self) -> float | None:
        """``None`` if any call could not be costed.

        Deliberately not a partial sum: a total that silently omits the
        unpriced calls reads as authoritative and is not.
        """
        if any(call.cost_usd is None for call in self.calls):
            return None
        return round(sum(call.cost_usd or 0.0 for call in self.calls), 6)
