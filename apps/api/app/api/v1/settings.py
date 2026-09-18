"""User preferences.

Stored in ``users.settings`` as a JSONB document, which is the right shape for
a handful of per-person UI choices: they change together, they are read as a
whole, and none of them is ever a query predicate.

**The document is validated on the way in and on the way out.** A blob column
accepts anything, including whatever an older build wrote and whatever a later
one will; reading it back through the same model means a key that is missing
gets today's default and a key that is no longer known is dropped rather than
being handed to a client that would not understand it.

**A PATCH is a merge over the validated current document, not over the raw
one.** Applying a partial update to unvalidated stored JSON would let an
unknown key survive by never being present at write time.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import AuditTrailDep, CurrentUser, UserRepositoryDep
from app.core.enums import AuditAction, LlmProvider
from app.core.errors import NotFound

router = APIRouter(tags=["settings"])


class UserSettings(BaseModel):
    """Mirrors `UserSettings` in @aether/shared-types.

    ``extra="forbid"`` on the update model rather than here: stored JSON may
    legitimately contain a key an older build wrote, and refusing to *read* it
    would make a person's settings page unreachable until someone migrated the
    column.
    """

    model_config = ConfigDict(extra="ignore")

    #: Which provider to prefer where routing allows an override. ``None``
    #: means "whatever the registry routes to", which is the default and the
    #: only setting most deployments should have.
    preferred_provider: LlmProvider | None = None
    #: A Literal rather than `ResearchMode`, which also has `conversational`:
    #: that mode belongs to a follow-up of an existing run, so it cannot be
    #: a default for a new one. `UserSettings` in @aether/shared-types says
    #: the same two values.
    default_mode: Literal["quick", "deep"] = "deep"
    default_depth: int = Field(default=3, ge=1, le=5)
    notify_on_completion: bool = False


class UserSettingsUpdate(BaseModel):
    """A partial update. Every field optional; unknown fields refused.

    Refused rather than ignored: a client sending ``notifyOnCompletion`` has a
    bug, and silently accepting the request would report success for a change
    that did not happen.
    """

    model_config = ConfigDict(extra="forbid")

    preferred_provider: LlmProvider | None = None
    default_mode: Literal["quick", "deep"] | None = None
    default_depth: int | None = Field(default=None, ge=1, le=5)
    notify_on_completion: bool | None = None


@router.get("/settings", response_model=UserSettings, summary="User preferences")
async def get_user_settings(user: CurrentUser, users: UserRepositoryDep) -> UserSettings:
    row = await users.get(user.id)
    if row is None:
        raise NotFound("That account no longer exists.", code="user_not_found")
    return UserSettings.model_validate(row.settings)


@router.patch("/settings", response_model=UserSettings, summary="Update preferences")
async def update_user_settings(
    body: UserSettingsUpdate,
    user: CurrentUser,
    users: UserRepositoryDep,
    trail: AuditTrailDep,
) -> UserSettings:
    row = await users.get(user.id)
    if row is None:
        raise NotFound("That account no longer exists.", code="user_not_found")

    current = UserSettings.model_validate(row.settings)
    # `exclude_unset` is what makes this a patch: a field the client did not
    # send is left alone, which is different from a field it sent as null -
    # `preferred_provider: null` is a real value meaning "no preference".
    changed = body.model_dump(exclude_unset=True)
    updated = current.model_copy(update=changed)

    stored = await users.replace_settings(user.id, updated.model_dump(mode="json"))
    if stored is None:
        raise NotFound("That account no longer exists.", code="user_not_found")

    await trail.record(
        AuditAction.SETTINGS_UPDATED,
        user_id=user.id,
        resource_type="user",
        resource_id=user.id,
        fields=sorted(changed),
    )
    return UserSettings.model_validate(stored)
