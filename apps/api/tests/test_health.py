"""Liveness and readiness.

The distinction being tested is operational, not cosmetic: liveness must never
depend on a downstream service, or a brief database outage becomes a container
restart storm.
"""

from __future__ import annotations

from httpx import AsyncClient


async def test_health_is_independent_of_dependencies(client: AsyncClient):
    """No Postgres is running in the test environment, and liveness still passes."""
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["environment"] == "test"
    assert body["version"]


async def test_ready_reports_503_when_a_dependency_is_down(client: AsyncClient):
    """Readiness really checks Postgres, which is absent here."""
    response = await client.get("/ready")

    assert response.status_code == 503
    body = response.json()
    assert body["ready"] is False

    by_name = {dependency["name"]: dependency for dependency in body["dependencies"]}
    assert by_name["postgres"]["ok"] is False
    assert by_name["postgres"]["detail"]
    # The in-memory queue selected by app_env=test is genuinely available.
    assert by_name["redis"]["ok"] is True


async def test_ready_reports_queue_depth(client: AsyncClient):
    response = await client.get("/ready")
    assert response.json()["queue_depth"] == 0


async def test_health_ready_alias_matches(client: AsyncClient):
    """TDD section 18 names the probe /health/ready; both paths must agree."""
    primary = await client.get("/ready")
    alias = await client.get("/health/ready")

    assert primary.status_code == alias.status_code
    assert primary.json()["ready"] == alias.json()["ready"]


async def test_responses_carry_a_request_id(client: AsyncClient):
    response = await client.get("/health")
    assert response.headers["x-request-id"]


async def test_an_upstream_request_id_is_preserved(client: AsyncClient):
    """A trace must survive a proxy hop rather than being replaced."""
    response = await client.get("/health", headers={"x-request-id": "upstream-trace-1"})
    assert response.headers["x-request-id"] == "upstream-trace-1"


async def test_security_headers_are_present(client: AsyncClient):
    response = await client.get("/health")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'none'" in response.headers["content-security-policy"]
