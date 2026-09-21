"""Single sign-on endpoints (FR-1, ADR 0022).

Three routes carry the whole flow, and the shape is forced by OAuth rather than
chosen: the browser must leave this origin and come back, so `start` and
`callback` are top-level redirects rather than the JSON endpoints everything
else here is.

**Every failure in `callback` ends the same way**: a redirect to the sign-in
page carrying one code from a closed set. Not a JSON error, because the caller
is a browser mid-navigation with nothing to render it; and not the upstream's
message, because that text is influenced by a third party and would be
reflected onto our own sign-in page. The set is in `@aether/shared-types` and
the frontend renders it from a lookup table.

**The transaction cookie and `state` are both checked**, and neither is
sufficient alone - see `app.auth.transactions` for the two different attacks
each one closes.

**Nothing here has completed a live round trip.** No Auth0 or Supabase
credentials exist on this machine. The flow is exercised end to end against a
scripted provider; treat the live path as unverified.
"""

from __future__ import annotations

import datetime as dt
import secrets
from typing import cast
from urllib.parse import quote, urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.api.deps import (
    AuditTrailDep,
    ClientAddress,
    CookiePolicyDep,
    CurrentSession,
    FederationServiceDep,
    IdentityRepositoryDep,
    ProvidersDep,
    RateLimit,
    SessionServiceDep,
    SettingsDep,
    TransactionStoreDep,
    UserAgent,
    UserRepositoryDep,
)
from app.auth.cookies import CookiePolicy
from app.auth.providers.base import ProviderName, ProviderRefused
from app.auth.redirects import safe_destination
from app.auth.transactions import PendingAuthorization, mint_handle
from app.core.config import Settings
from app.core.enums import AuditAction
from app.core.errors import AppError, LastIdentity, NotFound, RegistrationClosed
from app.core.logging import get_logger
from app.security.ratelimit import AUTH

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

#: Failure codes the callback may put in the URL. A closed set, mirrored by
#: `SsoFailure` in the shared types: the value lands in the address bar of a
#: page we render, so an open set would be a reflected-content injection.
_FAILURES = {
    "access_denied",
    "invalid_state",
    "exchange_failed",
    "email_unverified",
    "account_conflict",
    "provider_unavailable",
    "registration_closed",
    "unknown",
}


class SsoOptionResponse(BaseModel):
    """Mirrors `SsoOption` in @aether/shared-types."""

    provider: str
    connection: str
    label: str
    start_url: str


class SsoOptionsResponse(BaseModel):
    options: list[SsoOptionResponse]
    password_enabled: bool
    registration_enabled: bool


class IdentityResponse(BaseModel):
    """Mirrors `LinkedIdentity`. Never carries a token or a subject."""

    id: UUID
    provider: str
    connection: str | None
    email: str | None
    created_at: dt.datetime
    last_used_at: dt.datetime | None


_LABELS: dict[str, str] = {"google": "Google", "github": "GitHub"}


@router.get(
    "/sso/providers",
    response_model=SsoOptionsResponse,
    summary="Which sign-in methods this deployment offers",
)
async def sso_providers(
    providers: ProvidersDep,
    settings: SettingsDep,
) -> SsoOptionsResponse:
    """Served rather than compiled into the frontend.

    Which providers exist is a property of this deployment's configuration, and
    a button for an unconfigured provider is a dead end a person cannot tell
    apart from an outage.
    """
    options = [
        SsoOptionResponse(
            provider=name,
            connection=connection,
            label=_LABELS.get(connection, connection.title()),
            start_url=f"/api/v1/auth/sso/{name}/{connection}/start",
        )
        for name, provider in sorted(providers.items())
        for connection in provider.connections
    ]
    return SsoOptionsResponse(
        options=options,
        password_enabled=settings.password_login_enabled,
        # Both must hold for the "create one" link to mean anything: a
        # deployment can accept new accounts through SSO while refusing
        # password sign-up, and the link goes to the password form.
        registration_enabled=settings.registration_enabled and settings.password_login_enabled,
    )


@router.get(
    "/sso/{provider}/{connection}/start",
    status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    summary="Begin a single sign-on flow",
    dependencies=[Depends(RateLimit(AUTH))],
)
async def sso_start(
    provider: str,
    connection: str,
    providers: ProvidersDep,
    transactions: TransactionStoreDep,
    cookies: CookiePolicyDep,
    settings: SettingsDep,
    next: str | None = Query(default=None),
) -> RedirectResponse:
    """Redirect to the provider, remembering what the callback will need.

    Rate-limited like the credential endpoints: without it this is an
    unauthenticated endpoint that writes a store entry and makes an outbound
    request per call.
    """
    chosen = providers.get(provider)
    if chosen is None or connection not in chosen.connections:
        # Not "provider not found": naming which half was wrong tells an
        # unauthenticated caller how this deployment is configured.
        raise NotFound("That sign-in method is not available.", code="sso_not_available")

    # `connection` needs no cast: mypy narrows it to `Connection` from the
    # membership test above, because `chosen.connections` is typed. `provider`
    # gets one because a dict lookup narrows the value, not the key - and the
    # successful lookup is what establishes it.
    resolved_provider = cast("ProviderName", provider)

    redirect_uri = _callback_uri(settings, provider)
    request_ = chosen.authorize(
        connection=connection,
        redirect_uri=redirect_uri,
    )

    handle = mint_handle()
    await transactions.put(
        handle,
        PendingAuthorization(
            provider=resolved_provider,
            connection=connection,
            state=request_.state,
            nonce=request_.nonce,
            code_verifier=request_.code_verifier,
            redirect_uri=redirect_uri,
            # Validated here, at the only point a caller supplies it. The
            # callback then trusts its own stored value rather than a
            # parameter, so there is one place this rule is applied.
            destination=safe_destination(next),
        ),
        ttl_seconds=settings.sso_transaction_ttl_seconds,
    )

    redirect = RedirectResponse(request_.url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
    cookies.attach_transaction(redirect, handle)
    return redirect


@router.get(
    "/sso/{provider}/callback",
    summary="Complete a single sign-on flow",
    dependencies=[Depends(RateLimit(AUTH))],
)
async def sso_callback(
    provider: str,
    request: Request,
    providers: ProvidersDep,
    transactions: TransactionStoreDep,
    federation: FederationServiceDep,
    sessions: SessionServiceDep,
    cookies: CookiePolicyDep,
    settings: SettingsDep,
    trail: AuditTrailDep,
    address: ClientAddress,
    user_agent: UserAgent,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    """Verify the provider's response and issue this system's tokens."""
    handle = request.cookies.get(cookies.transaction_name)

    # Consumed first and unconditionally, before anything is decided with it.
    # A transaction must not survive a failed callback: leaving it in place
    # would let a captured `state` be retried until it expires.
    pending = await transactions.take(handle) if handle else None

    if error:
        # The provider declined, or the person pressed cancel. `error` is the
        # upstream's string and is never echoed - only used to pick between two
        # codes of ours.
        await trail.failure(AuditAction.LOGIN_FAILED, reason="sso_access_denied")
        return _failed(settings, cookies, "access_denied")

    if pending is None or not code or not state:
        return _failed(settings, cookies, "invalid_state")

    # Constant-time, because this is the comparison that decides whether a
    # callback belongs to this flow.
    if not secrets.compare_digest(pending.state, state) or pending.provider != provider:
        logger.warning("sso callback state mismatch", extra={"provider": provider})
        await trail.failure(AuditAction.LOGIN_FAILED, reason="sso_state_mismatch")
        return _failed(settings, cookies, "invalid_state")

    chosen = providers.get(provider)
    if chosen is None:
        return _failed(settings, cookies, "provider_unavailable")

    now = dt.datetime.now(dt.UTC)
    try:
        tokens = await chosen.exchange(
            code=code,
            code_verifier=pending.code_verifier,
            nonce=pending.nonce,
            redirect_uri=pending.redirect_uri,
        )
    except ProviderRefused:
        await trail.failure(AuditAction.LOGIN_FAILED, reason="sso_refused")
        return _failed(settings, cookies, "access_denied")
    except AppError as exc:
        logger.warning("sso exchange failed", extra={"provider": provider, "reason": exc.code})
        await trail.failure(AuditAction.LOGIN_FAILED, reason="sso_exchange_failed")
        return _failed(settings, cookies, "exchange_failed")

    if tokens.claims.usable_email is None and tokens.claims.email:
        # The provider knows an address but has not verified it. Reported
        # distinctly because it is the one failure the person can actually fix.
        await trail.failure(AuditAction.LOGIN_FAILED, reason="sso_email_unverified")
        return _failed(settings, cookies, "email_unverified")

    try:
        resolved = await federation.resolve(provider=provider, claims=tokens.claims, now=now)
    except RegistrationClosed:
        await trail.failure(AuditAction.REGISTER_REJECTED, reason="sso_registration_closed")
        return _failed(settings, cookies, "registration_closed")
    except AppError as exc:
        logger.info("sso account resolution refused", extra={"reason": exc.code})
        await trail.failure(AuditAction.LOGIN_FAILED, reason=exc.code)
        return _failed(settings, cookies, "account_conflict")

    issued = await sessions.start(
        user_id=resolved.user.id,
        email=resolved.user.email,
        role=resolved.user.role,
        provider=provider,
        user_agent=user_agent,
        ip=address,
        now=now,
    )

    redirect = RedirectResponse(
        _app_url(settings, pending.destination),
        status_code=status.HTTP_303_SEE_OTHER,
    )
    cookies.attach_tokens(redirect, access=issued.access_token, refresh=issued.refresh_token)
    cookies.clear_transaction(redirect)

    await trail.record(
        AuditAction.REGISTER if resolved.created else AuditAction.LOGIN,
        user_id=resolved.user.id,
        resource_type="session",
        resource_id=issued.session_id,
        provider=provider,
    )
    return redirect


@router.get(
    "/identities",
    response_model=list[IdentityResponse],
    summary="The upstream identities linked to this account",
)
async def list_identities(
    current: CurrentSession,
    identities: IdentityRepositoryDep,
) -> list[IdentityResponse]:
    rows = await identities.list_for_user(current.principal.id)
    return [
        IdentityResponse(
            id=row.id,
            provider=row.provider,
            connection=row.connection,
            email=row.email,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
        )
        for row in rows
    ]


@router.delete(
    "/identities/{identity_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unlink an upstream identity",
)
async def unlink_identity(
    identity_id: UUID,
    current: CurrentSession,
    identities: IdentityRepositoryDep,
    users: UserRepositoryDep,
    trail: AuditTrailDep,
) -> None:
    """Remove a provider link, unless it is the only way in.

    The check that matters: an account with no password and one identity
    becomes unreachable the moment that identity is unlinked. Refusing is the
    difference between a settings page and a way to lock yourself out - and it
    has to consider both credentials, because either one alone is enough to
    sign in and neither alone is enough to justify removing the other.
    """
    row = await users.get(current.principal.id)
    remaining = await identities.count_for_user(current.principal.id)
    if remaining <= 1 and not (row is not None and row.password_hash):
        raise LastIdentity()

    if not await identities.unlink(identity_id, user_id=current.principal.id):
        raise NotFound("No linked identity with that id.", code="identity_not_found")

    await trail.record(
        AuditAction.SESSION_REVOKED,
        user_id=current.principal.id,
        resource_type="identity",
        resource_id=identity_id,
    )


def _callback_uri(settings: Settings, provider: str) -> str:
    """Where the provider sends the browser back.

    Built from configuration, never from the request's own `Host` header. A
    callback URI taken from a header is attacker-controlled, and it is both
    what gets registered at the provider and what is repeated in the token
    request - so letting a request choose it is a redirect-URI manipulation
    primitive.
    """
    base = settings.sso_redirect_base_url or "http://localhost:8000/api/v1"
    return f"{base.rstrip('/')}/auth/sso/{quote(provider, safe='')}/callback"


def _app_url(settings: Settings, destination: str) -> str:
    return f"{settings.sso_app_base_url.rstrip('/')}{destination}"


def _failed(settings: Settings, cookies: CookiePolicy, failure: str) -> RedirectResponse:
    """Back to the sign-in page, carrying one code from the closed set.

    The code is looked up in `_FAILURES` rather than passed through, so that a
    future caller cannot put arbitrary text into a URL the sign-in page reads.
    The transaction cookie is cleared on every failure path: it named a
    transaction that has already been consumed, and leaving it set means the
    next attempt starts by presenting a dead handle.
    """
    code = failure if failure in _FAILURES else "unknown"
    redirect = RedirectResponse(
        f"{settings.sso_app_base_url.rstrip('/')}/login?{urlencode({'error': code})}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    cookies.clear_transaction(redirect)
    return redirect
