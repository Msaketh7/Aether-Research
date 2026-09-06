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


@router.get("/evaluations", summary="Benchmark results (Phase 18)")
async def get_evaluations() -> None:
    raise NotImplementedYet(
        "The evaluation suite is built in Phase 18. No benchmark has been executed, "
        "so there are no results to report.",
        code="evaluations_not_implemented",
    )


@router.get("/evaluations/system", summary="Live system metrics (Phase 17)")
async def get_system_metrics() -> None:
    raise NotImplementedYet(
        "System metrics come from the telemetry pipeline built in Phase 17.",
        code="metrics_not_implemented",
    )
