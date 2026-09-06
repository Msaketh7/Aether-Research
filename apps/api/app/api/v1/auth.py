"""Identity endpoints.

``/auth/me`` is real: it reports the principal the request actually resolved to,
which is what the frontend's account menu shows. The session-management surface
(register, login, logout, revoke) is FR-1 and lands in Phase 20; it is declared
here as explicitly not implemented so a caller can tell "not built yet" from
"wrong URL".
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import CurrentUser
from app.core.errors import NotImplementedYet

router = APIRouter(prefix="/auth", tags=["auth"])


class UserResponse(BaseModel):
    """Mirrors `User` in @aether/shared-types. Never carries a hash or a token."""

    id: UUID
    email: str
    name: str
    role: str
    created_at: datetime
    last_login_at: datetime | None


@router.get("/me", response_model=UserResponse, summary="The authenticated principal")
async def me(user: CurrentUser) -> UserResponse:
    now = datetime.now(UTC)
    return UserResponse(
        id=user.id,
        email=user.email,
        name=user.email.split("@")[0].replace(".", " ").title(),
        role=user.role,
        created_at=now,
        last_login_at=now,
    )


@router.get("/sessions", summary="List active sessions (Phase 20)")
async def sessions() -> None:
    raise NotImplementedYet(
        "Session management arrives with authentication in Phase 20.",
        code="auth_not_implemented",
    )
