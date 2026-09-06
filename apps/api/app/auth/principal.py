"""Who is making the request.

Authentication (Auth.js sessions, password hashing, session revocation) is
FR-1 and lands in Phase 20. **Authorisation is enforced now**: every research
object carries a ``user_id`` and every read is scoped by it, so the ownership
rules are exercised and tested from the first endpoint rather than retrofitted
onto a surface that already leaks.

Until real sessions exist the principal is resolved developmentally, and that
path is closed in production: a production deployment without Phase 20 returns
401 rather than silently serving a shared identity.
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
    if settings.is_production:
        # No real authentication is implemented yet. Refusing is the only safe
        # behaviour; a development identity in production would be a
        # catastrophic default.
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
