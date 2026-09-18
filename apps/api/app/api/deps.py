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

import datetime as dt
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.cookies import CookiePolicy
from app.auth.principal import DEV_USER_HEADER, Authenticated, Principal, authenticate
from app.auth.service import AuthService
from app.auth.sessions import SessionPolicy, SessionService
from app.core.config import Settings
from app.core.errors import Unauthenticated
from app.core.logging import request_id_var, user_id_var
from app.core.pagination import PageParams
from app.db.repositories.audit import SqlAlchemyAuditLog
from app.db.repositories.evaluations import SqlAlchemyEvaluationStore
from app.db.repositories.evidence import SqlAlchemyEvidenceRepository
from app.db.repositories.metrics import SqlAlchemySystemMetrics
from app.db.repositories.reports import SqlAlchemyReportRepository
from app.db.repositories.research import SqlAlchemyResearchRepository
from app.db.repositories.session import SessionRepository
from app.db.repositories.trace import SqlAlchemyTraceStore
from app.db.repositories.uploads import SqlAlchemyUploadRepository
from app.db.repositories.user import UserRepository
from app.db.session import Database
from app.models import LLMGateway
from app.observability.metrics import Metrics
from app.research.events import EventBroker
from app.research.service import ResearchService
from app.retrieval.uploads import UploadService
from app.security.audit import AuditTrail
from app.security.forwarded import client_address
from app.security.ratelimit import READ, RateLimiter, refuse
from app.sources import Toolbelt
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


def get_metrics(request: Request) -> Metrics | None:
    """This process's instruments, or ``None`` when metrics are switched off."""
    metrics: Metrics | None = getattr(request.app.state, "metrics", None)
    return metrics


def get_evaluation_store(
    database: Annotated[Database, Depends(get_database)],
) -> SqlAlchemyEvaluationStore:
    """The benchmark store. Takes the engine: it is written by a runner that
    has no request, and reading it here through the same object keeps one
    implementation rather than two."""
    return SqlAlchemyEvaluationStore(database)


def get_system_metrics(
    database: Annotated[Database, Depends(get_database)],
    queue: Annotated[JobQueue, Depends(get_queue)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> SqlAlchemySystemMetrics:
    """The live panel, read from rows and from the queue itself."""
    return SqlAlchemySystemMetrics(database, queue, lease_seconds=settings.worker_lease_seconds)


def get_broker(request: Request) -> EventBroker:
    broker: EventBroker = request.app.state.broker
    return broker


def get_object_storage(request: Request) -> ObjectStorage:
    storage: ObjectStorage = request.app.state.storage
    return storage


def get_toolbelt(request: Request) -> Toolbelt:
    """The process-wide research toolbelt.

    Handed out rather than constructed per request: it owns the guarded HTTP
    client and its connection pool, and a per-request one would re-fetch every
    robots.txt and give each request its own concurrency cap.
    """
    toolbelt: Toolbelt = request.app.state.toolbelt
    return toolbelt


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


def get_user_repository(session: SessionDep) -> UserRepository:
    return UserRepository(session)


def get_session_service(
    session: SessionDep,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> SessionService:
    return SessionService(
        SessionRepository(session),
        SessionPolicy(
            ttl_seconds=settings.session_ttl_seconds,
            max_per_user=settings.max_sessions_per_user,
        ),
    )


def get_auth_service(
    users: Annotated[UserRepository, Depends(get_user_repository)],
    sessions: Annotated[SessionService, Depends(get_session_service)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> AuthService:
    return AuthService(users=users, sessions=sessions, settings=settings)


def get_cookie_policy(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> CookiePolicy:
    return CookiePolicy.from_settings(settings)


def get_client_address(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> str | None:
    """Where the request came from, through the declared proxy hops."""
    return client_address(request, trusted_hops=settings.trusted_proxy_hops)


def get_user_agent(
    user_agent: Annotated[str | None, Header(alias="user-agent")] = None,
) -> str:
    return user_agent or ""


async def get_authenticated(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings_dep)],
    sessions: Annotated[SessionService, Depends(get_session_service)],
    users: Annotated[UserRepository, Depends(get_user_repository)],
    x_aether_user: Annotated[str | None, Header(alias=DEV_USER_HEADER)] = None,
) -> Authenticated | None:
    """Resolve the caller, or ``None`` if nothing identifies them.

    FastAPI caches a dependency's result for the life of a request, so the
    session lookup happens once however many things ask - the rate limiter
    asks before routing reaches the endpoint, and the endpoint asks again.

    The cookie is read off the request rather than declared as a parameter so
    that its name stays a setting rather than becoming part of the signature.
    """
    resolved = await authenticate(
        settings=settings,
        sessions=sessions,
        users=users,
        cookie_token=request.cookies.get(settings.session_cookie_name),
        user_header=x_aether_user,
        now=dt.datetime.now(dt.UTC),
    )
    if resolved is not None:
        # Bound here rather than at the endpoint, so that every log line a
        # request produces - including the ones from the rate limiter and the
        # error handlers - carries the user it was made by.
        user_id_var.set(str(resolved.principal.id))
    return resolved


def get_principal(
    resolved: Annotated[Authenticated | None, Depends(get_authenticated)],
) -> Authenticated:
    """The caller, or 401.

    Returns the whole :class:`Authenticated` because the session endpoints need
    to know *which* session is making the request in order to spare it.
    """
    if resolved is None:
        raise Unauthenticated()
    return resolved


async def get_current_user(
    resolved: Annotated[Authenticated, Depends(get_principal)],
    session: SessionDep,
) -> Principal:
    """The principal, guaranteed to have a row in ``users``.

    A caller resolved from a session cookie was resolved *through* their row,
    so there is nothing to do. The development identity has no row until it
    makes its first request, and every research run has a foreign key to
    ``users`` - so that one is created here, and only that one.
    """
    if resolved.session_id is None:
        await UserRepository(session).ensure(resolved.principal.id, resolved.principal.email)
    return resolved.principal


def get_rate_limiter(request: Request) -> RateLimiter:
    """The process-wide limiter. Owns its backend and its connection."""
    limiter: RateLimiter = request.app.state.rate_limiter
    return limiter


def get_audit_log(
    database: Annotated[Database, Depends(get_database)],
) -> SqlAlchemyAuditLog:
    """The audit store takes the engine, not the request's session.

    An audit row must survive the request that produced it failing, and the
    most valuable rows - a refused login, a rejected registration - are written
    on paths that end in an exception the request's transaction rolls back.
    """
    return SqlAlchemyAuditLog(database)


def get_audit_trail(
    log: Annotated[SqlAlchemyAuditLog, Depends(get_audit_log)],
    ip: Annotated[str | None, Depends(get_client_address)],
    user_agent: Annotated[str, Depends(get_user_agent)],
) -> AuditTrail:
    """This request's audit context, bound to the store."""
    return AuditTrail(log=log, ip=ip, user_agent=user_agent, request_id=request_id_var.get())


class RateLimit:
    """A dependency that spends one token from a named bucket.

    Written as a class so a route can declare *which* bucket it draws on:
    ``Depends(RateLimit(WRITE))``. The identity is the authenticated user where
    there is one and the client address otherwise - see
    ``app.security.ratelimit`` for why both are needed.
    """

    def __init__(self, rule: str = READ, *, cost: int = 1) -> None:
        self._rule = rule
        self._cost = cost

    async def __call__(
        self,
        limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
        resolved: Annotated[Authenticated | None, Depends(get_authenticated)],
        address: Annotated[str | None, Depends(get_client_address)],
    ) -> None:
        identity = (
            f"user:{resolved.principal.id}"
            if resolved is not None
            # An address-less transport (a test client, a unix socket) shares
            # one bucket rather than getting an unlimited one.
            else f"addr:{address or 'unknown'}"
        )
        decision = await limiter.check(self._rule, identity, cost=self._cost)
        if not decision.allowed:
            raise refuse(decision, rule=self._rule)


def get_research_repository(session: SessionDep) -> SqlAlchemyResearchRepository:
    return SqlAlchemyResearchRepository(session)


def get_upload_repository(session: SessionDep) -> SqlAlchemyUploadRepository:
    return SqlAlchemyUploadRepository(session)


def get_evidence_repository(session: SessionDep) -> SqlAlchemyEvidenceRepository:
    return SqlAlchemyEvidenceRepository(session)


def get_report_repository(session: SessionDep) -> SqlAlchemyReportRepository:
    return SqlAlchemyReportRepository(session)


def get_trace_store(database: Annotated[Database, Depends(get_database)]) -> SqlAlchemyTraceStore:
    """The trace store takes the engine, not the request's session.

    Unlike the other read models, this one is also written from the worker,
    where there is no request and no session to share - so it opens its own,
    and reading it here through the same object keeps one implementation
    rather than two.
    """
    return SqlAlchemyTraceStore(database)


def get_research_service(
    repository: Annotated[SqlAlchemyResearchRepository, Depends(get_research_repository)],
    uploads: Annotated[SqlAlchemyUploadRepository, Depends(get_upload_repository)],
    evidence: Annotated[SqlAlchemyEvidenceRepository, Depends(get_evidence_repository)],
    reports: Annotated[SqlAlchemyReportRepository, Depends(get_report_repository)],
    activity: Annotated[SqlAlchemyTraceStore, Depends(get_trace_store)],
    queue: Annotated[JobQueue, Depends(get_queue)],
    broker: Annotated[EventBroker, Depends(get_broker)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> ResearchService:
    """Composed per request, because the repositories are session-scoped.

    Both repositories share the request's one session - FastAPI resolves the
    session dependency once - so a run and its document attachments commit or
    roll back together. Cheap: the service holds references, opens no
    connections and does no work until a method is called.
    """
    return ResearchService(
        repository=repository,
        uploads=uploads,
        evidence_store=evidence,
        report_store=reports,
        activity_store=activity,
        queue=queue,
        broker=broker,
        settings=settings,
    )


def get_upload_service(
    repository: Annotated[SqlAlchemyUploadRepository, Depends(get_upload_repository)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> UploadService:
    return UploadService(
        repository=repository, storage=storage, max_bytes=settings.upload_limit_bytes
    )


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
MetricsDep = Annotated[Metrics | None, Depends(get_metrics)]
ObjectStorageDep = Annotated[ObjectStorage, Depends(get_object_storage)]
GatewayDep = Annotated[LLMGateway, Depends(get_gateway)]
ToolbeltDep = Annotated[Toolbelt, Depends(get_toolbelt)]
ResearchServiceDep = Annotated[ResearchService, Depends(get_research_service)]
UploadServiceDep = Annotated[UploadService, Depends(get_upload_service)]
CurrentUser = Annotated[Principal, Depends(get_current_user)]
#: The caller *and* the session that resolved them. Only the endpoints that act
#: on sessions need the second half; everything else takes `CurrentUser`.
CurrentSession = Annotated[Authenticated, Depends(get_principal)]
PageParamsDep = Annotated[PageParams, Depends(get_page_params)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
SessionServiceDep = Annotated[SessionService, Depends(get_session_service)]
UserRepositoryDep = Annotated[UserRepository, Depends(get_user_repository)]
CookiePolicyDep = Annotated[CookiePolicy, Depends(get_cookie_policy)]
AuditTrailDep = Annotated[AuditTrail, Depends(get_audit_trail)]
RateLimiterDep = Annotated[RateLimiter, Depends(get_rate_limiter)]
ClientAddress = Annotated[str | None, Depends(get_client_address)]
UserAgent = Annotated[str, Depends(get_user_agent)]
