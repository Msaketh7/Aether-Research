"""Research run lifecycle.

This is where the architecture's central rule lives: **the API never executes a
research workflow**. ``create`` validates, persists, publishes a started event
and enqueues a job. A worker picks it up (Phase 13) and runs the graph
(Phase 9). A multi-minute workflow inside a request thread is the failure mode
this whole layering exists to prevent (ADR 0001).

Every method takes the acting ``user_id`` and every read is scoped by it.
"""

from __future__ import annotations

import statistics
from uuid import UUID

from app.core.config import Settings
from app.core.enums import ResearchMode, RunStatus
from app.core.errors import (
    DependencyUnavailable,
    ReportNotReady,
    RunNotCancellable,
    RunNotFound,
    TooManyConcurrentRuns,
    ValidationFailed,
)
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.pagination import Page, PageParams, decode_cursor_id, encode_cursor
from app.evidence.schemas import EvidenceResponse
from app.reports.schemas import ReportResponse
from app.research.activity import ActivityResponse
from app.research.events import EventBroker, ResearchEvent, ResearchEventType
from app.research.repository import ResearchRepository, UploadAttachments, utcnow
from app.research.schemas import (
    CreateResearchRequest,
    CreateResearchResponse,
    DashboardStats,
    FollowUpRequest,
    ResearchPlan,
    ResearchRun,
    ResearchRunSummary,
    RunLimits,
    RunUsage,
    derive_title,
)
from app.sources.schemas import SourcesResponse
from app.workers.queue import JobQueue

logger = get_logger(__name__)


class ResearchService:
    """Orchestrates the run lifecycle across storage, the queue and the bus."""

    def __init__(
        self,
        *,
        repository: ResearchRepository,
        uploads: UploadAttachments,
        queue: JobQueue,
        broker: EventBroker,
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._uploads = uploads
        self._queue = queue
        self._broker = broker
        self._settings = settings

    # --- commands ---------------------------------------------------------

    async def create(self, user_id: UUID, request: CreateResearchRequest) -> CreateResearchResponse:
        """Queue a run. Returns ``202``-shaped data; performs no research."""
        active = await self._repository.count_active(user_id)
        if active >= self._settings.max_concurrent_runs_per_user:
            raise TooManyConcurrentRuns(
                "You already have "
                f"{self._settings.max_concurrent_runs_per_user} research runs in flight. "
                "Wait for one to finish or cancel it.",
                context={"active_runs": active},
            )

        if request.parent_run_id is not None:
            # A follow-up must reference a run the caller actually owns.
            parent = await self._repository.get(request.parent_run_id, user_id=user_id)
            if parent is None:
                raise RunNotFound("The run this follow-up refers to does not exist.")

        if request.document_ids:
            # Until Phase 7 these ids were accepted and silently dropped - the
            # failure the request schema's extra="forbid" exists to prevent,
            # reached by a field that *was* declared.
            owned = await self._uploads.owned_ids(user_id, request.document_ids)
            missing = [upload_id for upload_id in request.document_ids if upload_id not in owned]
            if missing:
                # Another user's upload is reported exactly like one that does
                # not exist, so an id cannot be probed for existence.
                raise ValidationFailed(
                    "Some attached documents could not be found.",
                    details={
                        "document_ids": [
                            f"No uploaded document with id {upload_id}." for upload_id in missing
                        ]
                    },
                )

        now = utcnow()
        run = ResearchRun(
            id=new_id(),
            user_id=user_id,
            parent_run_id=request.parent_run_id,
            title=derive_title(request.question),
            question=request.question,
            mode=request.mode,
            depth=request.resolved_depth(),
            domains=request.domains,
            date_range_start=request.date_range_start,
            date_range_end=request.date_range_end,
            status=RunStatus.QUEUED,
            progress=0.0,
            limits=RunLimits.for_mode(request.mode, self._settings),
            usage=RunUsage(),
            source_count=0,
            claim_count=0,
            contradiction_count=0,
            coverage_caveat=None,
            has_report=False,
            created_at=now,
            started_at=None,
            completed_at=None,
            error=None,
        )

        await self._repository.add(run)
        await self._uploads.attach(run.id, request.document_ids)
        # Commit before dispatching. Two things depend on this ordering: a
        # worker can never dequeue an id that does not resolve yet, and a
        # failure to dispatch leaves a durable `queued` run rather than
        # discarding what the user asked for.
        await self._repository.commit()

        await self._emit(
            run,
            ResearchEventType.RESEARCH_STARTED,
            {"question": run.question, "mode": run.mode.value},
        )

        try:
            await self._queue.enqueue(run.id)
        except Exception as exc:
            # A queue outage is a dependency failure, not an internal error: it
            # is retryable, and the run is already recorded. It stays `queued`
            # and is dispatched by the reconciliation sweep when the queue
            # recovers (ADR 0005), so the work is deferred, not lost.
            logger.warning(
                "failed to enqueue research run",
                extra={"run_id": str(run.id), "error": str(exc)},
            )
            raise DependencyUnavailable(
                "Your research run was saved but could not be started: the job queue "
                "is unavailable. It will begin automatically once the queue recovers.",
                code="queue_unavailable",
                context={"run_id": str(run.id)},
            ) from exc

        logger.info(
            "research run created",
            extra={"run_id": str(run.id), "mode": run.mode.value, "depth": run.depth},
        )
        return CreateResearchResponse(
            run_id=run.id,
            status=run.status,
            events_url=f"/api/v1/research/{run.id}/events",
        )

    async def follow_up(
        self, user_id: UUID, run_id: UUID, request: FollowUpRequest
    ) -> CreateResearchResponse:
        """PRD 5.3. A child run that will reuse the parent's evidence base."""
        parent = await self._require_run(user_id, run_id)
        return await self.create(
            user_id,
            CreateResearchRequest(
                question=request.question,
                mode=ResearchMode.CONVERSATIONAL,
                depth=parent.depth,
                parent_run_id=parent.id,
            ),
        )

    async def cancel(self, user_id: UUID, run_id: UUID) -> ResearchRun:
        """Cooperative cancellation: the worker stops at its next checkpoint."""
        run = await self._require_run(user_id, run_id)
        if not run.status.is_cancellable:
            raise RunNotCancellable(
                f"This run already {run.status.value}; there is nothing to cancel."
            )

        cancelled = run.model_copy(
            update={
                "status": RunStatus.CANCELLED,
                "completed_at": utcnow(),
            }
        )
        await self._repository.update(cancelled)
        await self._emit(cancelled, ResearchEventType.RESEARCH_CANCELLED, {"cancelled_by": "user"})
        logger.info("research run cancelled", extra={"run_id": str(run_id)})
        return cancelled

    # --- queries ----------------------------------------------------------

    async def get(self, user_id: UUID, run_id: UUID) -> ResearchRun:
        return await self._require_run(user_id, run_id)

    async def list_runs(
        self,
        user_id: UUID,
        *,
        params: PageParams,
        status: RunStatus | None = None,
        query: str | None = None,
    ) -> Page[ResearchRunSummary]:
        after_id: UUID | None = None
        if params.cursor:
            after_id = decode_cursor_id(params.cursor)

        runs, has_more = await self._repository.list_for_user(
            user_id,
            limit=params.limit,
            after_id=after_id,
            status=status,
            query=query,
        )
        items = [ResearchRunSummary.of(run) for run in runs]
        next_cursor = encode_cursor(str(items[-1].id)) if has_more and items else None
        return Page(items=items, next_cursor=next_cursor, total=None)

    async def stats(self, user_id: UUID) -> DashboardStats:
        runs = await self._repository.all_for_user(user_id)
        completed = [run for run in runs if run.status is RunStatus.COMPLETED]

        durations = [
            (run.completed_at - run.started_at).total_seconds()
            for run in completed
            if run.completed_at is not None and run.started_at is not None
        ]
        # Below three samples a median is noise, and the frontend renders None
        # as "not measured" rather than inventing a number.
        median_runtime = int(statistics.median(durations)) if len(durations) >= 3 else None

        return DashboardStats(
            total_runs=len(runs),
            completed_runs=len(completed),
            running_runs=sum(1 for run in runs if not run.status.is_terminal),
            failed_runs=sum(1 for run in runs if run.status is RunStatus.FAILED),
            total_sources=sum(run.source_count for run in runs),
            total_claims=sum(run.claim_count for run in runs),
            total_cost_usd=round(sum(run.usage.cost_usd for run in runs), 4),
            median_runtime_seconds=median_runtime,
        )

    # --- sub-resources ----------------------------------------------------
    #
    # These endpoints exist and are correct: a run that has produced nothing
    # returns nothing. They are wired to real data by the phases that create it
    # (6-8 for sources, 11 for evidence, 12 for reports, 9-10 for the trace).

    async def plan(self, user_id: UUID, run_id: UUID) -> ResearchPlan:
        run = await self._require_run(user_id, run_id)
        return ResearchPlan(research_goal=run.question, tasks=[], iteration=run.usage.iterations)

    async def sources(self, user_id: UUID, run_id: UUID) -> SourcesResponse:
        await self._require_run(user_id, run_id)
        return SourcesResponse(sources=[], clusters=[], next_cursor=None, total=0)

    async def evidence(self, user_id: UUID, run_id: UUID) -> EvidenceResponse:
        await self._require_run(user_id, run_id)
        return EvidenceResponse(claims=[], contradictions=[], next_cursor=None, total=0)

    async def activity(self, user_id: UUID, run_id: UUID) -> ActivityResponse:
        await self._require_run(user_id, run_id)
        return ActivityResponse(agent_runs=[], tool_calls=[], llm_calls=[])

    async def report(self, user_id: UUID, run_id: UUID) -> ReportResponse:
        run = await self._require_run(user_id, run_id)
        # A missing report is an expected state, not an error: the frontend
        # distinguishes `report_not_ready` from `run_not_found` and renders
        # each differently.
        raise ReportNotReady(
            "This run has not produced a validated report yet.",
            context={"run_status": run.status.value},
        )

    # --- events -----------------------------------------------------------

    async def history(self, user_id: UUID, run_id: UUID, *, after_seq: int) -> list[ResearchEvent]:
        await self._require_run(user_id, run_id)
        return await self._broker.history(run_id, after_seq=after_seq)

    # --- internals --------------------------------------------------------

    async def _require_run(self, user_id: UUID, run_id: UUID) -> ResearchRun:
        run = await self._repository.get(run_id, user_id=user_id)
        if run is None:
            # Deliberately indistinguishable from "belongs to someone else".
            raise RunNotFound()
        return run

    async def _emit(
        self,
        run: ResearchRun,
        event_type: ResearchEventType,
        payload: dict[str, object] | None = None,
    ) -> None:
        seq = await self._broker.next_seq(run.id)
        await self._broker.publish(
            ResearchEvent.build(
                seq=seq,
                type=event_type,
                run_id=run.id,
                status=run.status,
                payload=dict(payload or {}),
            )
        )
