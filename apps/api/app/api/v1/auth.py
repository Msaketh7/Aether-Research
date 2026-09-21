"""Identity endpoints (FR-1).

Registration, sign-in, sign-out, the current principal, and the session list a
person uses to sign a device out. The rules that make sign-in safe live in
``app.auth.service``; what is decided here is everything that is a property of
the *request* - the cookie, the rate-limit bucket, and the audit row.

**Two buckets guard the credential endpoints, not one.** The client address
bounds one attacker trying many accounts. The submitted address bounds many
clients trying one account, which is what credential stuffing looks like and
what an address-only limit misses entirely. The address bucket is spent
*before* the password is verified, so a refusal costs an attacker a round trip
and costs this process no Argon2.

**A refused login still writes an audit row**, which is the reason the audit
store has a transaction of its own: the refusal raises, and the request's own
transaction is rolled back.
"""

from __future__ import annotations

import datetime as dt
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field

from app.api.deps import (
    AuditTrailDep,
    AuthServiceDep,
    ClientAddress,
    CookiePolicyDep,
    CurrentSession,
    CurrentUser,
    RateLimit,
    RateLimiterDep,
    SessionServiceDep,
    UserAgent,
    UserRepositoryDep,
)
from app.api.errors import error_body
from app.auth.service import normalise_email
from app.core.enums import AuditAction
from app.core.errors import AppError, NotFound, Unauthenticated
from app.db.models.user import UserRow
from app.security.ratelimit import AUTH, WRITE, refuse

router = APIRouter(prefix="/auth", tags=["auth"])


class UserResponse(BaseModel):
    """Mirrors `User` in @aether/shared-types. Never carries a hash or a token."""

    id: UUID
    email: str
    name: str
    role: str
    created_at: dt.datetime
    last_login_at: dt.datetime | None


class LoginRequest(BaseModel):
    email: EmailStr
    #: Only bounded here. The floor is a policy applied at registration, and
    #: applying it to sign-in too would tell an attacker that a short guess is
    #: not worth making - and would lock out anybody whose password predates a
    #: raised floor.
    password: str = Field(min_length=1, max_length=1024)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)
    name: str = Field(default="", max_length=200)


class LoginResponse(BaseModel):
    """What the frontend stores in its query cache after signing in."""

    user: UserResponse


class SessionResponse(BaseModel):
    """One live session, as `/settings` lists it."""

    id: UUID
    user_agent: str
    ip: str
    created_at: dt.datetime
    expires_at: dt.datetime
    #: True for the session making this request. The UI must not offer to
    #: revoke it without saying that it signs the caller out.
    current: bool


class RevokedResponse(BaseModel):
    revoked: int


def _user_view(row: UserRow) -> UserResponse:
    return UserResponse.model_validate(row, from_attributes=True)


@router.post(
    "/register",
    response_model=LoginResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account and sign in",
    dependencies=[Depends(RateLimit(WRITE))],
)
async def register(
    body: RegisterRequest,
    response: Response,
    service: AuthServiceDep,
    cookies: CookiePolicyDep,
    limiter: RateLimiterDep,
    trail: AuditTrailDep,
    address: ClientAddress,
    user_agent: UserAgent,
) -> LoginResponse:
    await _spend_credential_budget(limiter, address=address, email=body.email)

    now = dt.datetime.now(dt.UTC)
    try:
        signed_in = await service.register(
            email=body.email,
            password=body.password,
            name=body.name,
            user_agent=user_agent,
            ip=address,
            now=now,
        )
    except AppError as exc:
        await trail.failure(AuditAction.REGISTER_REJECTED, reason=exc.code)
        raise

    cookies.attach_tokens(
        response,
        access=signed_in.tokens.access_token,
        refresh=signed_in.tokens.refresh_token,
    )
    await trail.record(
        AuditAction.REGISTER,
        user_id=signed_in.user.id,
        resource_type="session",
        resource_id=signed_in.tokens.session_id,
    )
    return LoginResponse(user=_user_view(signed_in.user))


@router.post(
    "/login",
    response_model=LoginResponse,
    summary="Start a session",
)
async def login(
    body: LoginRequest,
    response: Response,
    service: AuthServiceDep,
    cookies: CookiePolicyDep,
    limiter: RateLimiterDep,
    trail: AuditTrailDep,
    address: ClientAddress,
    user_agent: UserAgent,
) -> LoginResponse:
    await _spend_credential_budget(limiter, address=address, email=body.email)

    now = dt.datetime.now(dt.UTC)
    try:
        signed_in = await service.sign_in(
            email=body.email,
            password=body.password,
            user_agent=user_agent,
            ip=address,
            now=now,
        )
    except AppError as exc:
        # The address is recorded, the password is not, and the user id is not
        # looked up - an audit row must not become the account-enumeration
        # oracle the endpoint itself refuses to be.
        await trail.failure(
            AuditAction.LOGIN_FAILED, email=normalise_email(body.email), reason=exc.code
        )
        raise

    cookies.attach_tokens(
        response,
        access=signed_in.tokens.access_token,
        refresh=signed_in.tokens.refresh_token,
    )
    await trail.record(
        AuditAction.LOGIN,
        user_id=signed_in.user.id,
        resource_type="session",
        resource_id=signed_in.tokens.session_id,
    )
    return LoginResponse(user=_user_view(signed_in.user))


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="End the current session",
)
async def logout(
    response: Response,
    current: CurrentSession,
    sessions: SessionServiceDep,
    cookies: CookiePolicyDep,
    trail: AuditTrailDep,
) -> None:
    """Revoke this session and clear the cookie.

    Both halves, and in that order: clearing the cookie alone would leave a
    working token in whatever else holds it, and revoking alone would leave the
    browser presenting a dead cookie on every request.
    """
    if current.session_id is not None:
        await sessions.revoke(
            current.session_id,
            user_id=current.principal.id,
            now=dt.datetime.now(dt.UTC),
        )
        await trail.record(
            AuditAction.LOGOUT,
            user_id=current.principal.id,
            resource_type="session",
            resource_id=current.session_id,
        )
    cookies.clear(response)


@router.post(
    "/refresh",
    response_model=LoginResponse,
    summary="Renew the token pair",
    dependencies=[Depends(RateLimit(AUTH))],
)
async def refresh(
    request: Request,
    response: Response,
    sessions: SessionServiceDep,
    users: UserRepositoryDep,
    cookies: CookiePolicyDep,
) -> LoginResponse | JSONResponse:
    """Rotate the refresh token and mint a new access token.

    **The path matters.** The refresh cookie is scoped to exactly this path, so
    that the credential which can mint sessions is absent from every other
    request. Moving this endpoint without moving `REFRESH_COOKIE_PATH` means
    the browser stops sending the cookie and every renewal fails.

    **CSRF is handled by `SameSite`**, not by a token. A cross-site POST does
    not carry a `Lax` cookie, so a forged request arrives with no credential
    and is refused. That is the same control protecting every other
    state-changing endpoint here; a separate CSRF token would add a second
    mechanism for one endpoint without closing anything the first leaves open.

    Rate-limited in the credential class: it accepts a bearer credential from
    an unauthenticated caller, which is precisely what that bucket is for.
    """
    presented = request.cookies.get(cookies.refresh_name)
    if not presented:
        raise Unauthenticated("Sign in to continue.", code="unauthenticated")

    async def load_identity(user_id: UUID) -> tuple[str, str] | None:
        row = await users.get(user_id)
        return (row.email, row.role) if row is not None else None

    now = dt.datetime.now(dt.UTC)
    try:
        issued, session_row = await sessions.refresh(
            presented, load_identity=load_identity, now=now
        )
    except AppError as exc:
        # **Returned, not raised.** Clearing the cookies on the injected
        # `response` and then raising does nothing: the exception handler
        # builds a fresh response and the mutated one is discarded, so the
        # browser keeps a refresh token that is known to be dead. Rendering the
        # same envelope here is the only way to refuse *and* clear in one
        # response. Caught by `test_a_failed_refresh_clears_both_cookies`.
        refusal = JSONResponse(
            status_code=exc.status_code,
            content=error_body(code=exc.code, message=exc.message, details=exc.details),
            headers=exc.headers,
        )
        cookies.clear(refusal)
        return refusal

    # Present by construction: `load_identity` returning None is what makes
    # `refresh` refuse, so reaching here means the row was read.
    user = await users.get(session_row.user_id)
    if user is None:
        refusal = JSONResponse(
            status_code=401,
            content=error_body(code="unauthenticated", message="Sign in to continue."),
        )
        cookies.clear(refusal)
        return refusal

    cookies.attach_tokens(response, access=issued.access_token, refresh=issued.refresh_token)
    return LoginResponse(user=_user_view(user))


@router.get("/me", response_model=UserResponse, summary="The authenticated principal")
async def me(user: CurrentUser, users: UserRepositoryDep) -> UserResponse:
    row = await users.get(user.id)
    if row is None:
        # The row is created for the development identity by `get_current_user`
        # and exists by construction for a real session, so this is a broken
        # invariant rather than an expected outcome.
        raise NotFound("That account no longer exists.", code="user_not_found")
    return _user_view(row)


@router.get(
    "/sessions",
    response_model=list[SessionResponse],
    summary="List active sessions",
)
async def list_sessions(
    current: CurrentSession,
    sessions: SessionServiceDep,
) -> list[SessionResponse]:
    rows = await sessions.list_active(current.principal.id, now=dt.datetime.now(dt.UTC))
    return [
        SessionResponse(
            id=row.id,
            user_agent=row.user_agent,
            # The column is nullable - a request can arrive without a usable
            # address - and the DTO says a string, so "unknown" is the honest
            # rendering rather than an invented address.
            ip=str(row.ip) if row.ip else "unknown",
            created_at=row.created_at,
            expires_at=row.expires_at,
            current=row.id == current.session_id,
        )
        for row in rows
    ]


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke one session",
)
async def revoke_session(
    session_id: UUID,
    response: Response,
    current: CurrentSession,
    sessions: SessionServiceDep,
    cookies: CookiePolicyDep,
    trail: AuditTrailDep,
) -> None:
    revoked = await sessions.revoke(
        session_id, user_id=current.principal.id, now=dt.datetime.now(dt.UTC)
    )
    if not revoked:
        # Scoped by user in SQL, so another person's session id is simply not
        # found - the 404 confirms nothing about whether it exists.
        raise NotFound("No active session with that id.", code="session_not_found")

    await trail.record(
        AuditAction.SESSION_REVOKED,
        user_id=current.principal.id,
        resource_type="session",
        resource_id=session_id,
    )
    if session_id == current.session_id:
        # Revoking your own session is a logout; leaving the cookie in place
        # would mean every later request carried a token known to be dead.
        cookies.clear(response)


@router.delete(
    "/sessions",
    response_model=RevokedResponse,
    summary="Revoke every other session",
)
async def revoke_other_sessions(
    current: CurrentSession,
    sessions: SessionServiceDep,
    trail: AuditTrailDep,
) -> RevokedResponse:
    """Sign out every device but this one.

    The control a person reaches for when they think a password has leaked, so
    it spares the session making the request: signing yourself out as well
    would mean signing back in with the credential you are worried about.
    """
    revoked = await sessions.revoke_others(
        user_id=current.principal.id,
        keep=current.session_id,
        now=dt.datetime.now(dt.UTC),
    )
    await trail.record(AuditAction.SESSIONS_REVOKED, user_id=current.principal.id, revoked=revoked)
    return RevokedResponse(revoked=revoked)


async def _spend_credential_budget(
    limiter: RateLimiterDep,
    *,
    address: str | None,
    email: str,
) -> None:
    """Both credential buckets, before any password is hashed.

    Separate identities in the same bucket class, so one attacker cannot use
    the allowance of the account they are attacking and vice versa.
    """
    for identity in (f"addr:{address or 'unknown'}", f"email:{normalise_email(email)}"):
        decision = await limiter.check(AUTH, identity)
        if not decision.allowed:
            raise refuse(decision, rule=AUTH)
