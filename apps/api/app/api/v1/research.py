"""The research API surface (TDD section 18).

The contract the frontend was built against in Phase 1, implemented for real.
Two rules are structural rather than incidental:

* ``POST /research`` returns **202 Accepted**. It persists and enqueues; it
  never runs a workflow.
* every path is scoped to the authenticated user, and a run that is not theirs
  is indistinguishable from one that does not exist.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from app.answers.schemas import AnswerResponse
from app.api.deps import (
    AuditTrailDep,
    BrokerDep,
    CurrentUser,
    PageParamsDep,
    RateLimit,
    ResearchServiceDep,
    SettingsDep,
)
from app.core.enums import AuditAction, ClaimStatus, RunStatus, SourceType
from app.core.logging import get_logger
from app.core.pagination import Page
from app.evidence.schemas import EvidenceResponse
from app.reports.schemas import ReportResponse
from app.research.activity import ActivityResponse
from app.research.schemas import (
    CreateResearchRequest,
    CreateResearchResponse,
    DashboardStats,
    FollowUpRequest,
    ResearchPlan,
    ResearchRun,
    ResearchRunSummary,
)
from app.security.ratelimit import WRITE
from app.sources.schemas import SourcesResponse

logger = get_logger(__name__)

router = APIRouter(prefix="/research", tags=["research"])


# --- collection -----------------------------------------------------------


@router.post(
    "",
    response_model=CreateResearchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a research run",
    # The tighter bucket: this one call commits a worker, a budget and every
    # model call a run makes, which is not the same as a page view.
    dependencies=[Depends(RateLimit(WRITE))],
)
async def create_research(
    payload: CreateResearchRequest,
    user: CurrentUser,
    service: ResearchServiceDep,
    trail: AuditTrailDep,
) -> CreateResearchResponse:
    """202: the run is queued. A worker executes it (ADR 0001)."""
    created = await service.create(user.id, payload)
    await trail.record(
        AuditAction.RUN_CREATED,
        user_id=user.id,
        resource_type="research_run",
        resource_id=created.run_id,
        mode=str(payload.mode),
    )
    return created


@router.get("", response_model=Page[ResearchRunSummary], summary="List the caller's runs")
async def list_research(
    user: CurrentUser,
    service: ResearchServiceDep,
    params: PageParamsDep,
    status_filter: RunStatus | None = None,
    q: str | None = None,
) -> Page[ResearchRunSummary]:
    return await service.list_runs(user.id, params=params, status=status_filter, query=q)


@router.get("/stats", response_model=DashboardStats, summary="Dashboard aggregates")
async def research_stats(user: CurrentUser, service: ResearchServiceDep) -> DashboardStats:
    # Declared before /{run_id} so the literal segment wins the route match.
    return await service.stats(user.id)


# --- one run --------------------------------------------------------------


@router.get("/{run_id}", response_model=ResearchRun, summary="Run status and summary")
async def get_research(run_id: UUID, user: CurrentUser, service: ResearchServiceDep) -> ResearchRun:
    return await service.get(user.id, run_id)


@router.get("/{run_id}/plan", response_model=ResearchPlan, summary="Planner subtasks")
async def get_plan(run_id: UUID, user: CurrentUser, service: ResearchServiceDep) -> ResearchPlan:
    return await service.plan(user.id, run_id)


@router.get("/{run_id}/sources", response_model=SourcesResponse, summary="Discovered sources")
async def get_sources(
    run_id: UUID,
    user: CurrentUser,
    service: ResearchServiceDep,
    page: PageParamsDep,
    type: Annotated[SourceType | None, Query(description="Only sources of this kind.")] = None,
) -> SourcesResponse:
    # ``type`` rather than ``source_type``: it is the query parameter the web app
    # has sent since Phase 1, and the wire contract is the one that cannot be
    # changed unilaterally. FastAPI's shadowing of the builtin is local to the
    # signature and the value is an enum by the time it is used.
    return await service.sources(user.id, run_id, page=page, source_type=type)


@router.get("/{run_id}/evidence", response_model=EvidenceResponse, summary="Claims and evidence")
async def get_evidence(
    run_id: UUID,
    user: CurrentUser,
    service: ResearchServiceDep,
    page: PageParamsDep,
    status: Annotated[ClaimStatus | None, Query(description="Only claims in this state.")] = None,
) -> EvidenceResponse:
    return await service.evidence(user.id, run_id, page=page, status=status)


@router.get("/{run_id}/activity", response_model=ActivityResponse, summary="Agent trace")
async def get_activity(
    run_id: UUID, user: CurrentUser, service: ResearchServiceDep
) -> ActivityResponse:
    return await service.activity(user.id, run_id)


@router.get("/{run_id}/report", response_model=ReportResponse, summary="Validated report")
async def get_report(
    run_id: UUID, user: CurrentUser, service: ResearchServiceDep
) -> ReportResponse:
    """404 `report_not_ready` until synthesis and citation validation have run.

    Distinct from `run_not_found`: the frontend renders the two differently.
    """
    return await service.report(user.id, run_id)


@router.get("/{run_id}/answer", response_model=AnswerResponse, summary="The direct answer")
async def get_answer(
    run_id: UUID, user: CurrentUser, service: ResearchServiceDep
) -> AnswerResponse:
    """The plain-prose answer, or `answer: null` while the run is still working.

    Not a 404 when there is none: unlike the report, this is the body of a
    conversation the client is already rendering, and "not yet" is the normal
    state of a run that started ten seconds ago. A client watching the run is
    served the same text by `answer_delta` events as it is written; this is what
    a reader who was not watching gets.
    """
    return await service.answer(user.id, run_id)


@router.post("/{run_id}/cancel", response_model=ResearchRun, summary="Cancel a run")
async def cancel_research(
    run_id: UUID, user: CurrentUser, service: ResearchServiceDep, trail: AuditTrailDep
) -> ResearchRun:
    run = await service.cancel(user.id, run_id)
    await trail.record(
        AuditAction.RUN_CANCELLED,
        user_id=user.id,
        resource_type="research_run",
        resource_id=run_id,
    )
    return run


@router.post(
    "/{run_id}/followup",
    response_model=CreateResearchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Queue a follow-up run",
    dependencies=[Depends(RateLimit(WRITE))],
)
async def follow_up(
    run_id: UUID,
    payload: FollowUpRequest,
    user: CurrentUser,
    service: ResearchServiceDep,
    trail: AuditTrailDep,
) -> CreateResearchResponse:
    created = await service.follow_up(user.id, run_id, payload)
    await trail.record(
        AuditAction.RUN_FOLLOWUP,
        user_id=user.id,
        resource_type="research_run",
        resource_id=created.run_id,
        parent_run_id=str(run_id),
    )
    return created


# --- progress stream ------------------------------------------------------


def _parse_last_event_id(raw: str | None) -> int:
    """A malformed header replays from the start rather than failing the stream."""
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


async def _event_stream(
    *,
    request: Request,
    service: ResearchServiceDep,
    broker: BrokerDep,
    settings: SettingsDep,
    user_id: UUID,
    run_id: UUID,
    after_seq: int,
) -> AsyncIterator[bytes]:
    """Replay, then follow, then stop.

    The subscription is opened *before* the replay is read, so an event
    published between the two is delivered rather than lost in the gap.
    """
    heartbeat = settings.sse_heartbeat_seconds
    deadline = asyncio.get_running_loop().time() + settings.sse_max_connection_seconds

    subscription = broker.subscribe(run_id)
    delivered = after_seq

    try:
        # Tell the browser how long to wait before reconnecting.
        yield f"retry: {heartbeat * 200}\n\n".encode()

        for event in await service.history(user_id, run_id, after_seq=after_seq):
            delivered = max(delivered, event.seq)
            yield event.to_sse_frame().encode()
            if event.type.is_terminal:
                return

        # A run that finished before this connection opened has nothing left to
        # stream; holding the connection open would be a leak, not a feature.
        run = await service.get(user_id, run_id)
        if run.status.is_terminal:
            return

        while True:
            if await request.is_disconnected():
                return
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                # Close deliberately; the browser reconnects with
                # Last-Event-ID and resumes exactly where it stopped.
                return

            try:
                async with asyncio.timeout(min(heartbeat, remaining)):
                    event = await anext(subscription)
            except (TimeoutError, StopAsyncIteration):
                yield b": heartbeat\n\n"
                continue

            if event.seq <= delivered:
                continue
            delivered = event.seq
            yield event.to_sse_frame().encode()
            if event.type.is_terminal:
                return
    finally:
        with contextlib.suppress(Exception):
            await subscription.aclose()


@router.get(
    "/{run_id}/events",
    summary="Server-Sent Events progress stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_events(
    run_id: UUID,
    request: Request,
    user: CurrentUser,
    service: ResearchServiceDep,
    broker: BrokerDep,
    settings: SettingsDep,
    last_event_id: str | None = Header(default=None, alias="last-event-id"),
) -> Response:
    """ADR 0006. Ownership is checked before a single byte is streamed."""
    await service.get(user.id, run_id)
    after_seq = _parse_last_event_id(last_event_id)

    return StreamingResponse(
        _event_stream(
            request=request,
            service=service,
            broker=broker,
            settings=settings,
            user_id=user.id,
            run_id=run_id,
            after_seq=after_seq,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Nginx and friends buffer streamed responses unless told not to.
            "X-Accel-Buffering": "no",
        },
    )
