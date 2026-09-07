"""Dependency injection.

Process-lifetime objects (settings, engine, queue, event broker) live on
``app.state`` and are assembled once at startup. Request-lifetime objects - a
database session, the repositories bound to it, and the service composed from
them - are built here, per request.

The split matters: a session is a transaction, and a transaction that outlives
a request either holds a connection open or silently commits work from an
unrelated one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import DEV_USER_HEADER, Principal, resolve_principal
from app.core.config import Settings
from app.core.logging import user_id_var
from app.core.pagination import PageParams
from app.db.repositories.research import SqlAlchemyResearchRepository
from app.db.repositories.user import UserRepository
from app.db.session import Database
from app.models import LLMGateway
from app.research.events import EventBroker
from app.research.service import ResearchService
from app.storage import ObjectStorage
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


def get_object_storage(request: Request) -> ObjectStorage:
    storage: ObjectStorage = request.app.state.storage
    return storage


def get_gateway(request: Request) -> LLMGateway:
    """The process-wide model gateway.

    Handed out rather than constructed per request: it owns the concurrency
    semaphore that makes fan-out safe, and one semaphore per request is none.
    """
    gateway: LLMGateway = request.app.state.gateway
    return gateway


async def get_session(
    database: Annotated[Database, Depends(get_database)],
) -> AsyncIterator[AsyncSession]:
    """One transaction per request.

    Commits when the handler returns, rolls back if it raises. A handler
    therefore cannot leave a half-written run behind by returning early.
    """
    async with database.session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


def get_principal(
    settings: Annotated[Settings, Depends(get_settings_dep)],
    x_aether_user: Annotated[str | None, Header(alias=DEV_USER_HEADER)] = None,
) -> Principal:
    """Resolve the caller and bind their id to the logging context."""
    principal = resolve_principal(settings, x_aether_user)
    user_id_var.set(str(principal.id))
    return principal


async def get_current_user(
    principal: Annotated[Principal, Depends(get_principal)],
    session: SessionDep,
) -> Principal:
    """The principal, guaranteed to have a row in ``users``.

    Every research run has a foreign key to that table, so this is what makes
    the development principal usable before Phase 20 adds registration. In
    production the row is created at sign-up and this is a no-op.
    """
    await UserRepository(session).ensure(principal.id, principal.email)
    return principal


def get_research_repository(session: SessionDep) -> SqlAlchemyResearchRepository:
    return SqlAlchemyResearchRepository(session)


def get_research_service(
    repository: Annotated[SqlAlchemyResearchRepository, Depends(get_research_repository)],
    queue: Annotated[JobQueue, Depends(get_queue)],
    broker: Annotated[EventBroker, Depends(get_broker)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ResearchService:
    """Composed per request, because the repository is session-scoped.

    Cheap: the service holds references, opens no connections and does no work
    until a method is called.
    """
    return ResearchService(repository=repository, queue=queue, broker=broker, settings=settings)


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
ObjectStorageDep = Annotated[ObjectStorage, Depends(get_object_storage)]
GatewayDep = Annotated[LLMGateway, Depends(get_gateway)]
ResearchServiceDep = Annotated[ResearchService, Depends(get_research_service)]
CurrentUser = Annotated[Principal, Depends(get_current_user)]
PageParamsDep = Annotated[PageParams, Depends(get_page_params)]
