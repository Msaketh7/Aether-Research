"""Agent, tool and model call records: the step-by-step trace (FR-10).

Three linked layers that together answer "why did the run do that?" without
needing server logs. Written by the worker as it runs the graph (Phase 16,
``app.observability.ledger``) and read back through ``ActivityStore``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.core.enums import (
    AgentName,
    AgentStatus,
    LlmCallStatus,
    LlmProvider,
    ToolName,
    ToolStatus,
)
from app.research.schemas import ApiModel, RunError


class AgentRunRecord(ApiModel):
    id: UUID
    run_id: UUID
    task_external_id: str | None
    agent_name: AgentName
    iteration: int
    status: AgentStatus
    summary: str
    latency_ms: int | None
    tokens: int
    cost_usd: float
    trace_id: str | None
    span_id: str | None
    error: RunError | None
    started_at: datetime
    completed_at: datetime | None


class ToolCallRecord(ApiModel):
    id: UUID
    agent_run_id: UUID
    tool_name: ToolName
    request_summary: str
    status: ToolStatus
    latency_ms: int
    cache_hit: bool
    retries: int
    result_summary: str
    started_at: datetime


class LlmCallRecord(ApiModel):
    """One model call with the accounting Phase 16 requires on every call."""

    id: UUID
    agent_run_id: UUID | None
    role: AgentName
    provider: LlmProvider
    model: str
    prompt_version: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    latency_ms: int
    cache_hit: bool
    status: LlmCallStatus
    created_at: datetime


class ActivityResponse(ApiModel):
    agent_runs: list[AgentRunRecord]
    tool_calls: list[ToolCallRecord]
    llm_calls: list[LlmCallRecord]


class ActivityStore(Protocol):
    """Where the trace is read from.

    The protocol lives beside the DTOs, and the Postgres implementation lives
    in ``app/db/repositories/trace.py`` - the same arrangement as the evidence
    and report stores.
    """

    async def activity_for(self, run_id: UUID) -> ActivityResponse:
        """This run's trace, bounded. Empty for a run that has not started."""
        ...
