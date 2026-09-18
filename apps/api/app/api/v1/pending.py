"""Endpoints whose implementing phase has not landed.

Declared rather than omitted, for two reasons: the OpenAPI document describes
the full intended surface, and a client receives `not_implemented` instead of a
bare 404, so "not built yet" is distinguishable from "wrong URL".

Returning plausible-looking empty data here would be worse than either - it
would let a caller believe a capability exists.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import NotImplementedYet

router = APIRouter(tags=["pending"])


@router.get("/settings", summary="User preferences (Phase 20)")
async def get_settings() -> None:
    raise NotImplementedYet(
        "User preferences are stored with the accounts system in Phase 20.",
        code="settings_not_implemented",
    )
