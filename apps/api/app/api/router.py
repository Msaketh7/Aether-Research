"""Router assembly.

Health probes live at the root because orchestrators and load balancers expect
them there and must not be coupled to an API version. Everything else is under
``/api/v1``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, files, health, pending, research

#: Unversioned: liveness and readiness.
probe_router = APIRouter()
probe_router.include_router(health.router)

#: Versioned application surface.
api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(auth.router)
api_v1_router.include_router(research.router)
api_v1_router.include_router(files.router)
api_v1_router.include_router(pending.router)
