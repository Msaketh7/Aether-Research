"""Router assembly.

Health probes live at the root because orchestrators and load balancers expect
them there and must not be coupled to an API version. Everything else is under
``/api/v1``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, files, health, pending, research, telemetry

#: Unversioned: liveness, readiness, and the Prometheus scrape. A scraper is
#: not an API client, and the path is pinned by
#: ``infra/monitoring/prometheus.yml``.
probe_router = APIRouter()
probe_router.include_router(health.router)
probe_router.include_router(telemetry.metrics_router)

#: Versioned application surface.
api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(auth.router)
api_v1_router.include_router(research.router)
api_v1_router.include_router(files.router)
# Before `pending`, whose `/evaluations/system` placeholder this replaces:
# the first match wins, and a 501 shadowing a real endpoint would be a
# capability that exists and says it does not.
api_v1_router.include_router(telemetry.system_router)
api_v1_router.include_router(pending.router)
