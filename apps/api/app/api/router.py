"""Router assembly.

Health probes live at the root because orchestrators and load balancers expect
them there and must not be coupled to an API version. Everything else is under
``/api/v1``.

**Rate limiting is attached here, once, to the whole versioned surface** (Phase
20). A per-endpoint decoration would mean a new endpoint is unlimited until
somebody remembers - the failure mode a rate limit exists to prevent. Routes
that start real work draw on a second, tighter bucket as well, declared on the
route itself; the credential endpoints add a third, keyed by the address being
attempted.

The probe router is deliberately outside it: a load balancer's health check is
not a client, and throttling it is how a healthy replica gets taken out of
service.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import RateLimit
from app.api.v1 import auth, files, health, research, settings, telemetry

#: Unversioned: liveness, readiness, and the Prometheus scrape. A scraper is
#: not an API client, and the path is pinned by
#: ``infra/monitoring/prometheus.yml``.
probe_router = APIRouter()
probe_router.include_router(health.router)
probe_router.include_router(telemetry.metrics_router)

#: Versioned application surface.
api_v1_router = APIRouter(prefix="/api/v1", dependencies=[Depends(RateLimit())])
api_v1_router.include_router(auth.router)
api_v1_router.include_router(research.router)
api_v1_router.include_router(files.router)
api_v1_router.include_router(settings.router)
api_v1_router.include_router(telemetry.system_router)
