"""Behaviour when a backing service is unavailable.

The distinction being protected: a dependency outage is *retryable* and must not
be reported as an internal error. The frontend's `ApiError.isRetryable` and the
readiness probe both act on that classification, so getting it wrong turns a
transient blip into a permanent-looking failure.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import API, valid_request


class BrokenQueue:
    """A queue whose every operation fails, as Redis being down looks."""

    async def enqueue(self, run_id: object) -> None:
        raise ConnectionError("Error 111 connecting to redis:6379. Connection refused.")

    async def depth(self) -> int:
        raise ConnectionError("Connection refused.")

    async def check(self) -> bool:
        return False

    async def close(self) -> None:
        return None


@pytest.fixture
def broken_queue_client(client: AsyncClient) -> AsyncClient:
    """Swap the queue on the live app the client is already bound to.

    Only ``app.state.queue`` needs replacing: the research service is composed
    per request from whatever the state holds, so the next request picks this
    up without any further patching.
    """
    app = client._transport.app  # type: ignore[attr-defined]
    app.state.queue = BrokenQueue()
    return client


async def test_a_queue_outage_is_503_not_500(broken_queue_client: AsyncClient):
    response = await broken_queue_client.post(f"{API}/research", json=valid_request())

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "queue_unavailable"
    # The connection string and the driver's message stay in the log.
    assert "redis:6379" not in response.text


async def test_the_run_is_kept_so_the_work_is_deferred_not_lost(
    broken_queue_client: AsyncClient,
):
    """ADR 0005: the queue is a dispatch mechanism, not the source of truth."""
    await broken_queue_client.post(f"{API}/research", json=valid_request())

    runs = (await broken_queue_client.get(f"{API}/research")).json()["items"]

    assert len(runs) == 1
    assert runs[0]["status"] == "queued"


async def test_the_error_tells_the_user_what_happened_to_their_run(
    broken_queue_client: AsyncClient,
):
    response = await broken_queue_client.post(f"{API}/research", json=valid_request())

    message = response.json()["error"]["message"]
    assert "saved" in message
    assert "queue" in message


async def test_readiness_fails_when_the_queue_is_down(broken_queue_client: AsyncClient):
    response = await broken_queue_client.get("/ready")

    assert response.status_code == 503
    by_name = {dep["name"]: dep for dep in response.json()["dependencies"]}
    assert by_name["redis"]["ok"] is False
    # Depth is unknown, not zero: an empty queue and an unreachable one are
    # different facts.
    assert response.json()["queue_depth"] is None


async def test_reads_still_work_while_the_queue_is_down(broken_queue_client: AsyncClient):
    """A dispatch outage must not take down the read surface."""
    response = await broken_queue_client.get(f"{API}/research")
    assert response.status_code == 200


class BrokenObjectStorage:
    """An artifact store that cannot be reached, as MinIO being down looks."""

    async def check(self) -> bool:
        return False

    async def close(self) -> None:
        return None


async def test_readiness_fails_when_the_artifact_store_is_down(client: AsyncClient):
    """A run that cannot persist a fetched PDF produces a report whose citations
    point at nothing, so an unreachable store must stop traffic."""
    app = client._transport.app  # type: ignore[attr-defined]
    app.state.storage = BrokenObjectStorage()

    response = await client.get("/ready")

    assert response.status_code == 503
    by_name = {dep["name"]: dep for dep in response.json()["dependencies"]}
    assert by_name["object-storage"]["ok"] is False
    # The other dependencies are still reported honestly rather than blanked.
    assert by_name["postgres"]["ok"] is True


async def test_liveness_survives_an_artifact_store_outage(client: AsyncClient):
    """Storage is not part of liveness; a MinIO blip must not restart the API."""
    app = client._transport.app  # type: ignore[attr-defined]
    app.state.storage = BrokenObjectStorage()

    assert (await client.get("/health")).status_code == 200
