"""Research run DTOs.

These serialise to exactly the shapes in ``@aether/shared-types``: field names,
nullability and enum values all match, because the frontend was built against
that contract in Phase 1 and must not need a change to talk to this service
(ADR 0009).

Request models validate; response models describe. They are separate types on
purpose - a client must never be able to set ``status`` or ``cost_usd``.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import Settings
from app.core.enums import ResearchMode, RunStatus, TaskPriority, TaskStatus

MIN_QUESTION_LENGTH = 15
MAX_QUESTION_LENGTH = 2000
MAX_DOMAINS = 10
MIN_DEPTH = 1
MAX_DEPTH = 5


class ApiModel(BaseModel):
    """Base for every response DTO."""

    model_config = ConfigDict(from_attributes=True, extra="forbid")


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class CreateResearchRequest(BaseModel):
    """FR-2. Validated here and mirrored by the client-side schema in the web app.

    Extra fields are rejected rather than ignored: silently dropping a field a
    caller believed was applied is worse than a 422.
    """

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=MIN_QUESTION_LENGTH, max_length=MAX_QUESTION_LENGTH)
    mode: ResearchMode
    depth: int | None = Field(default=None, ge=MIN_DEPTH, le=MAX_DEPTH)
    domains: list[str] = Field(default_factory=list, max_length=MAX_DOMAINS)
    date_range_start: date | None = None
    date_range_end: date | None = None
    document_ids: list[UUID] = Field(default_factory=list)
    parent_run_id: UUID | None = None

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        stripped = value.strip()
        if len(stripped) < MIN_QUESTION_LENGTH:
            raise ValueError(
                f"A research question must be at least {MIN_QUESTION_LENGTH} characters."
            )
        return stripped

    @field_validator("domains")
    @classmethod
    def _normalise_domains(cls, value: list[str]) -> list[str]:
        """Accept a bare domain only.

        A full URL here would be a filter the search layer cannot honour, and
        accepting one would quietly produce results the user did not ask for.
        """
        normalised: list[str] = []
        for raw in value:
            domain = raw.strip().lower()
            if domain.startswith(("http://", "https://")) or "/" in domain:
                raise ValueError("Enter bare domains such as sec.gov, without a scheme or path.")
            if not domain or "." not in domain or " " in domain:
                raise ValueError(f"{raw!r} is not a valid domain.")
            if domain not in normalised:
                normalised.append(domain)
        return normalised

    @model_validator(mode="after")
    def _check_date_range(self) -> CreateResearchRequest:
        if (
            self.date_range_start is not None
            and self.date_range_end is not None
            and self.date_range_start > self.date_range_end
        ):
            raise ValueError("The end date must not be before the start date.")
        return self

    def resolved_depth(self) -> int:
        """Quick runs make a single pass, so depth is meaningless for them."""
        if self.mode is ResearchMode.QUICK:
            return 1
        return self.depth if self.depth is not None else 3


class FollowUpRequest(BaseModel):
    """PRD 5.3: a child run seeded with the parent's evidence base."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=10, max_length=MAX_QUESTION_LENGTH)

    @field_validator("question")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class CreateResearchResponse(ApiModel):
    """``202 Accepted``. The work is queued, not performed."""

    run_id: UUID
    status: RunStatus
    events_url: str


class RunLimits(ApiModel):
    """The FR-8 ceilings applied to a run, echoed so the UI can show headroom."""

    max_iterations: int
    max_sources: int
    max_runtime_seconds: int
    max_cost_usd: float

    @classmethod
    def for_mode(cls, mode: ResearchMode, settings: Settings) -> RunLimits:
        if mode is ResearchMode.QUICK:
            # A quick run does one retrieval round with no critic loop, so its
            # ceilings are tighter than the global maxima by design.
            return cls(
                max_iterations=1,
                max_sources=min(12, settings.max_sources),
                max_runtime_seconds=min(60, settings.max_runtime_seconds),
                max_cost_usd=min(0.50, settings.max_estimated_cost_usd),
            )
        return cls(
            max_iterations=settings.max_research_iterations,
            max_sources=settings.max_sources,
            max_runtime_seconds=settings.max_runtime_seconds,
            max_cost_usd=settings.max_estimated_cost_usd,
        )


class RunUsage(ApiModel):
    """Consumption against :class:`RunLimits`."""

    iterations: int = 0
    sources: int = 0
    elapsed_seconds: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


class ResearchTask(ApiModel):
    """One planner subtask (FR-3)."""

    id: UUID
    run_id: UUID
    external_id: str
    question: str
    priority: TaskPriority
    status: TaskStatus
    rationale: str
    iteration: int
    source_count: int = 0
    claim_count: int = 0
    completed_at: datetime | None = None


class ResearchPlan(ApiModel):
    research_goal: str
    tasks: list[ResearchTask]
    iteration: int


class RunError(ApiModel):
    code: str
    message: str


class ResearchRun(ApiModel):
    """The central object of the product."""

    id: UUID
    user_id: UUID
    parent_run_id: UUID | None
    title: str
    question: str
    mode: ResearchMode
    depth: int
    domains: list[str]
    date_range_start: date | None
    date_range_end: date | None
    status: RunStatus
    progress: float = Field(ge=0.0, le=1.0)
    limits: RunLimits
    usage: RunUsage
    source_count: int
    claim_count: int
    contradiction_count: int
    coverage_caveat: str | None
    has_report: bool
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: RunError | None


class ResearchRunSummary(ApiModel):
    """Compact row for the dashboard and history list."""

    id: UUID
    title: str
    question: str
    mode: ResearchMode
    status: RunStatus
    progress: float
    source_count: int
    claim_count: int
    contradiction_count: int
    cost_usd: float
    created_at: datetime
    completed_at: datetime | None

    @classmethod
    def of(cls, run: ResearchRun) -> ResearchRunSummary:
        return cls(
            id=run.id,
            title=run.title,
            question=run.question,
            mode=run.mode,
            status=run.status,
            progress=run.progress,
            source_count=run.source_count,
            claim_count=run.claim_count,
            contradiction_count=run.contradiction_count,
            cost_usd=run.usage.cost_usd,
            created_at=run.created_at,
            completed_at=run.completed_at,
        )


class DashboardStats(ApiModel):
    """Aggregates for the dashboard header.

    ``median_runtime_seconds`` is ``None`` when there are too few completed runs
    to be meaningful. The frontend renders that as "not measured" rather than
    as zero, which is why it must not be defaulted here.
    """

    total_runs: int
    completed_runs: int
    running_runs: int
    failed_runs: int
    total_sources: int
    total_claims: int
    total_cost_usd: float
    median_runtime_seconds: int | None


def derive_title(question: str, *, max_length: int = 68) -> str:
    """A short label for a run, taken from its first clause."""
    first_clause = question.split(".")[0].split("?")[0].strip()
    candidate = first_clause or question.strip()
    if len(candidate) > max_length:
        candidate = candidate[: max_length - 1].rstrip() + "…"
    return candidate or "Untitled research"
