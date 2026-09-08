"""Who is making the request.

Authentication (Auth.js sessions, password hashing, session revocation) is
FR-1 and lands in Phase 20. **Authorisation is enforced now**: every research
object carries a ``user_id`` and every read is scoped by it, so the ownership
rules are exercised and tested from the first endpoint rather than retrofitted
onto a surface that already leaks.

Until real sessions exist the principal is resolved developmentally. That path is
open **only** in `local` and `test`. Any other environment returns 401 rather
than serving a shared identity.

The allowlist is deliberately the shape it is. Gating on "not production" would
leave `staging` open, and a staging deployment is usually internet-reachable
with a copy of real data - so `X-Aether-User: <any uuid>` would be a complete
authentication bypass on the environment people forget to lock down. A new
environment added to the `Environment` literal is closed by default under this
rule and open by default under the other one, which is the direction a security
gate should fail.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.config import Settings
from app.core.errors import Unauthenticated

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


def resolve_principal(settings: Settings, user_header: str | None) -> Principal:
    """Resolve the caller, or refuse.

    :param user_header: value of the development ``X-Aether-User`` header, used
        only outside production to act as a different user.
    """
    if settings.app_env not in DEVELOPMENT_ENVIRONMENTS:
        # No real authentication is implemented yet. Refusing is the only safe
        # behaviour; a development identity anywhere reachable would be a
        # complete authentication bypass.
        raise Unauthenticated(
            "Authentication is not available in this build.",
            code="authentication_not_configured",
        )

    if user_header:
        try:
            return Principal(id=UUID(user_header), email=f"{user_header}@aether.dev")
        except ValueError as exc:
            raise Unauthenticated("That development user id is not a valid UUID.") from exc

    return DEV_PRINCIPAL
