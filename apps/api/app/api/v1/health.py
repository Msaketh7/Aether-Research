"""Liveness and readiness.

The two answer different questions and must not be conflated:

* ``/health`` - is this process alive? If it answers at all, the answer is yes.
  An orchestrator restarts the container when this fails, so it must never
  depend on a downstream service; otherwise a brief database blip becomes a
  restart storm.
* ``/ready`` - can this process serve traffic? It really checks Postgres and
  Redis, and returns 503 when it cannot, so a load balancer stops sending
  requests without the container being killed.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.api.deps import DatabaseDep, QueueDep, SettingsDep
from app.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["health"])

API_VERSION = "0.1.0"


class HealthResponse(BaseModel):
    status: Literal["ok"]
    version: str
    environment: str


class DependencyStatus(BaseModel):
    name: str
    ok: bool
    detail: str | None = None


class ReadyResponse(BaseModel):
    ready: bool
    version: str
    environment: str
    dependencies: list[DependencyStatus]
    #: Pending research jobs. ``None`` when the queue could not be reached -
    #: never 0, which would falsely read as "the queue is empty".
    queue_depth: int | None = None


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health(settings: SettingsDep) -> HealthResponse:
    return HealthResponse(status="ok", version=API_VERSION, environment=settings.app_env)


@router.get("/ready", response_model=ReadyResponse, summary="Readiness probe")
@router.get("/health/ready", response_model=ReadyResponse, include_in_schema=False)
async def ready(
    response: Response,
    settings: SettingsDep,
    database: DatabaseDep,
    queue: QueueDep,
) -> ReadyResponse:
    """Probe every hard dependency concurrently, with a ceiling.

    A readiness check that can hang is worse than one that fails: the
    orchestrator learns nothing while the timeout runs.
    """
    try:
        async with asyncio.timeout(5):
            database_ok, queue_ok = await asyncio.gather(database.check(), queue.check())
    except TimeoutError:
        logger.warning("readiness probe timed out")
        database_ok = queue_ok = False

    depth: int | None = None
    if queue_ok:
        try:
            depth = await queue.depth()
        except Exception as exc:
            logger.warning("queue depth unavailable", extra={"error": str(exc)})

    dependencies = [
        DependencyStatus(
            name="postgres",
            ok=database_ok,
            detail=None if database_ok else "Not reachable.",
        ),
        DependencyStatus(
            name="redis",
            ok=queue_ok,
            detail=None if queue_ok else "Not reachable.",
        ),
    ]

    is_ready = all(dependency.ok for dependency in dependencies)
    if not is_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadyResponse(
        ready=is_ready,
        version=API_VERSION,
        environment=settings.app_env,
        dependencies=dependencies,
        queue_depth=depth,
    )
