"""Who is making the request.

Since ADR 0022 the caller presents a **signed access token** in an `HttpOnly`
cookie. It is verified by signature rather than looked up, then checked against
the revocation index so that signing a device out takes effect on its next
request. The user row is still read, because a token's claims are a snapshot
and a role change must not wait for expiry. Authorisation was
already enforced from Phase 2 - every research object carries a ``user_id`` and
every read is scoped by it - so authentication slotted in underneath rules that
were already being exercised, rather than being retrofitted onto a surface that
already leaked.

**The development identity survives, narrowed.** ``X-Aether-User: <uuid>`` acts
as that user, and an unauthenticated request in a development environment acts
as the default developer. That path is open **only** in `local` and `test`, and
only while ``DEV_IDENTITY_ENABLED`` is on - a flag that can close the gate
further but never open it, because the environment allowlist is checked first.
Any other environment returns 401 rather than serving a shared identity.

The allowlist is deliberately the shape it is. Gating on "not production" would
leave `staging` open, and a staging deployment is usually internet-reachable
with a copy of real data - so `X-Aether-User: <any uuid>` would be a complete
authentication bypass on the environment people forget to lock down. A new
environment added to the `Environment` literal is closed by default under this
rule and open by default under the other one, which is the direction a security
gate should fail.

**Order matters, and the explicit wins.** The header is checked before the
cookie: it is an instruction to act as somebody, where a cookie is ambient
state a browser or a test client carries along. Reversing them would mean a
test that signs in can no longer act as a second user, which is how the
cross-user authorisation tests are written.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.auth.sessions import SessionService
from app.core.config import Settings
from app.core.errors import Unauthenticated
from app.db.repositories.user import UserRepository

# Stable so a developer's runs survive a restart of the in-memory repository
# within one session, and so the frontend's fixtures line up.
DEV_USER_ID = UUID("00000000-0000-4000-8000-000000000001")

#: Development-only header that selects the acting user. It exists so the
#: cross-user authorisation tests can be written against the real dependency
#: chain instead of a mocked one.
DEV_USER_HEADER = "x-aether-user"

#: The only environments in which an unauthenticated caller may act as a user.
#: An allowlist, not a "not production" check - see the module docstring.
DEVELOPMENT_ENVIRONMENTS = frozenset({"local", "test"})


class Principal(BaseModel):
    """The authenticated caller."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    email: str
    role: str = "user"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


DEV_PRINCIPAL = Principal(id=DEV_USER_ID, email="analyst@aether.dev", role="user")


@dataclass(frozen=True, slots=True)
class Authenticated:
    """A resolved caller and, when there is one, the session that resolved them.

    ``session_id`` is ``None`` for the development identity, which has no
    session row. Endpoints that act on sessions - logout, "sign out my other
    devices" - use it to spare the caller's own, and treat ``None`` as "there
    is nothing of mine to sign out".
    """

    principal: Principal
    session_id: UUID | None = None


def development_identity_allowed(settings: Settings) -> bool:
    """Whether an unauthenticated caller may act as a user.

    Two conditions, and the environment allowlist is the one that matters: the
    flag can only ever narrow it.
    """
    return settings.app_env in DEVELOPMENT_ENVIRONMENTS and settings.dev_identity_enabled


def resolve_development_principal(settings: Settings, user_header: str | None) -> Principal:
    """The development identity, or a refusal.

    Kept as a function of its own so that the gate has one implementation and
    one test, rather than being re-expressed at each caller.
    """
    if not development_identity_allowed(settings):
        raise Unauthenticated(
            "Sign in to continue.",
            code="unauthenticated",
        )

    if user_header:
        try:
            return Principal(id=UUID(user_header), email=f"{user_header}@aether.dev")
        except ValueError as exc:
            raise Unauthenticated("That development user id is not a valid UUID.") from exc

    return DEV_PRINCIPAL


async def authenticate(
    *,
    settings: Settings,
    sessions: SessionService,
    users: UserRepository,
    cookie_token: str | None,
    user_header: str | None,
    now: dt.datetime,
) -> Authenticated | None:
    """Resolve the caller, or ``None`` if nothing identifies them.

    Returning ``None`` rather than raising, because two callers need different
    things from the same answer: the endpoints refuse, and the rate limiter
    falls back to keying by client address. Only one of those is an error.
    """
    if user_header and development_identity_allowed(settings):
        return Authenticated(resolve_development_principal(settings, user_header))

    if cookie_token:
        try:
            # Signature, issuer, audience, expiry - and the revocation index,
            # which is what makes a signed-out device stop working on its next
            # request rather than whenever its token happens to expire.
            claims = await sessions.verify(cookie_token, now=now)
        except Unauthenticated:
            # A cookie that does not verify is *not* an anonymous request in a
            # development environment: falling through to the shared identity
            # would make a revoked session look like a working one, which is
            # the single behaviour revocation exists to prevent.
            return None

        # The row is still read. The token carries the email and role it was
        # minted with, and those go stale: a role revoked ten minutes ago must
        # not keep working until the token expires. The token says *who*; the
        # database says what they may currently do.
        user = await users.get(claims.user_id)
        if user is None:
            return None

        return Authenticated(
            Principal(id=user.id, email=user.email, role=user.role),
            session_id=claims.session_id,
        )

    if development_identity_allowed(settings):
        return Authenticated(DEV_PRINCIPAL)

    return None
