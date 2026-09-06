"""Dependency injection.

Everything a route needs is resolved here from ``app.state``, which is
assembled once at startup. Routes therefore have no knowledge of how a
repository, queue or broker is constructed, which is what lets Phase 3 swap the
in-memory repository for Postgres without touching a single endpoint.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request

from app.auth.principal import DEV_USER_HEADER, Principal, resolve_principal
from app.core.config import Settings
from app.core.logging import user_id_var
from app.core.pagination import PageParams
from app.db.session import Database
from app.research.events import EventBroker
from app.research.service import ResearchService
from app.workers.queue import JobQueue


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


def get_queue(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.queue
    return queue


def get_broker(request: Request) -> EventBroker:
    broker: EventBroker = request.app.state.broker
    return broker


def get_research_service(request: Request) -> ResearchService:
    service: ResearchService = request.app.state.research_service
    return service


def get_principal(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    x_aether_user: Annotated[str | None, Header(alias=DEV_USER_HEADER)] = None,
) -> Principal:
    """Resolve the caller and bind their id to the logging context."""
    principal = resolve_principal(settings, x_aether_user)
    user_id_var.set(str(principal.id))
    return principal


def get_page_params(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    limit: int | None = None,
    cursor: str | None = None,
) -> PageParams:
    """Clamp pagination at the boundary; an unbounded list never reaches the
    repository."""
    return PageParams.clamped(
        limit,
        cursor,
        default=settings.default_page_size,
        maximum=settings.max_page_size,
    )


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
DatabaseDep = Annotated[Database, Depends(get_database)]
QueueDep = Annotated[JobQueue, Depends(get_queue)]
BrokerDep = Annotated[EventBroker, Depends(get_broker)]
ResearchServiceDep = Annotated[ResearchService, Depends(get_research_service)]
CurrentUser = Annotated[Principal, Depends(get_principal)]
PageParamsDep = Annotated[PageParams, Depends(get_page_params)]
