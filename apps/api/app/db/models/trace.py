"""The execution trace: agent runs, tool calls and model calls.

Three linked layers that answer "why did the run do that?" without server logs.
``llm_calls`` also carries the accounting Phase 16 requires on every call -
provider, model, tokens, latency and cost - which is what makes per-run budgets
enforceable and cost attributable to an agent role rather than to the month.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    AgentName,
    AgentStatus,
    LlmCallStatus,
    LlmProvider,
    ToolName,
    ToolStatus,
)
from app.db.base import Base, TimestampMixin, fk_uuid, uuid_pk
from app.db.models.research import enum_check

if TYPE_CHECKING:
    from app.db.models.research import ResearchRunRow


class AgentRunRow(Base, TimestampMixin):
    """One agent execution."""

    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = fk_uuid(
        ForeignKey("research_tasks.id", ondelete="SET NULL")
    )
    task_external_id: Mapped[str | None] = mapped_column(String(80))

    agent_name: Mapped[str] = mapped_column(String(30), nullable=False)
    iteration: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))

    #: Full input and output, for replay and for evaluation attribution.
    #: Truncated payloads point at object storage rather than bloating a row.
    input: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    output: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    latency_ms: Mapped[int | None] = mapped_column(Integer)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    #: ``None`` is *not measured*: a step that called a model the registry does
    #: not price has no cost, which is a different fact from costing nothing
    #: (migration 0011).
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 4))
    #: OpenTelemetry ids, so a row links to a trace once Phase 17 lands.
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    span_id: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    started_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    completed_at: Mapped[dt.datetime | None]

    run: Mapped[ResearchRunRow] = relationship(back_populates="agent_runs")
    tool_calls: Mapped[list[ToolCallRow]] = relationship(
        back_populates="agent_run", cascade="all, delete-orphan", passive_deletes=True
    )
    llm_calls: Mapped[list[LlmCallRow]] = relationship(
        back_populates="agent_run", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        enum_check("agent_name", AgentName, "agent_runs_agent_name"),
        enum_check("status", AgentStatus, "agent_runs_status"),
        # The activity page: this run's agents in execution order.
        Index("ix_agent_runs_run_id_started_at", "run_id", "started_at"),
    )


class ToolCallRow(Base, TimestampMixin):
    """One external action: a search, a fetch, an API call."""

    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_run_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False
    )
    tool_name: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    request: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: Truncated; a large body lives in object storage under `storage_key`.
    response_summary: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    storage_key: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(20), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cost_usd: Mapped[float] = mapped_column(
        Numeric(10, 4), nullable=False, server_default=text("0")
    )
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    retries: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    span_id: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[dt.datetime] = mapped_column(nullable=False)

    agent_run: Mapped[AgentRunRow] = relationship(back_populates="tool_calls")

    __table_args__ = (
        enum_check("tool_name", ToolName, "tool_calls_tool_name"),
        enum_check("status", ToolStatus, "tool_calls_status"),
        # Tool reliability and latency metrics, by tool and outcome.
        Index("ix_tool_calls_tool_name_status", "tool_name", "status"),
    )


class LlmCallRow(Base, TimestampMixin):
    """One model call, with the accounting Phase 16 requires."""

    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = uuid_pk()
    #: Nullable: a call can be made outside an agent, for example by the
    #: evaluation judge.
    agent_run_id: Mapped[uuid.UUID | None] = fk_uuid(
        ForeignKey("agent_runs.id", ondelete="CASCADE")
    )
    run_id: Mapped[uuid.UUID | None] = fk_uuid(ForeignKey("research_runs.id", ondelete="CASCADE"))

    role: Mapped[str] = mapped_column(String(30), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    #: From packages/prompts, so a quality change is attributable to a prompt
    #: edit rather than guessed at.
    prompt_version: Mapped[str] = mapped_column(
        String(80), nullable=False, server_default=text("''")
    )

    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    completion_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    #: ``None`` is *not measured*. See ``agent_runs.cost_usd`` above.
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 4))
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    temperature: Mapped[float | None] = mapped_column(Numeric(3, 2))
    cache_hit: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Hash of the request, for de-duplication and cache lookup.
    request_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    span_id: Mapped[str | None] = mapped_column(String(32))

    agent_run: Mapped[AgentRunRow | None] = relationship(back_populates="llm_calls")

    __table_args__ = (
        enum_check("role", AgentName, "llm_calls_role"),
        enum_check("provider", LlmProvider, "llm_calls_provider"),
        enum_check("status", LlmCallStatus, "llm_calls_status"),
        # Cost attribution: spend per run, and per model over a window.
        Index("ix_llm_calls_run_id_created_at", "run_id", "created_at"),
        Index("ix_llm_calls_provider_model_created_at", "provider", "model", "created_at"),
    )
