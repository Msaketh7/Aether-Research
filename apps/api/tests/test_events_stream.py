"""The Server-Sent Events progress stream (ADR 0006).

The frontend's `EventSource` hook was written in Phase 1 against this exact
frame format. These tests pin the parts it depends on: named events, an ``id:``
on every frame, and replay from ``Last-Event-ID`` so a reconnect does not
duplicate or lose events.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

from httpx import AsyncClient

from app.core.config import Settings
from app.core.enums import ResearchEventType, RunStatus
from app.db.session import Database
from app.research.eventbus import build_event_broker
from app.research.events import EventDraft
from tests.conftest import API, as_user, valid_request


async def read_frames(
    client: AsyncClient, run_id: str, *, limit: int = 1, **kwargs: object
) -> list[dict]:
    """Read up to ``limit`` data frames, then close the stream.

    Bounded by a timeout so a stalled stream fails the test instead of hanging
    the suite.
    """
    frames: list[dict] = []
    async with asyncio.timeout(10):
        async with client.stream("GET", f"{API}/research/{run_id}/events", **kwargs) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")

            current: dict[str, str] = {}
            async for line in response.aiter_lines():
                if line.startswith("id:"):
                    current["id"] = line.removeprefix("id:").strip()
                elif line.startswith("event:"):
                    current["event"] = line.removeprefix("event:").strip()
                elif line.startswith("data:"):
                    current["data"] = line.removeprefix("data:").strip()
                elif line == "" and "data" in current:
                    frames.append(
                        {
                            "id": current.get("id"),
                            "event": current.get("event"),
                            "data": json.loads(current["data"]),
                        }
                    )
                    current = {}
                    if len(frames) >= limit:
                        break
    return frames


async def create_run(client: AsyncClient) -> str:
    response = await client.post(f"{API}/research", json=valid_request())
    return str(response.json()["run_id"])


async def test_the_stream_replays_the_started_event(client: AsyncClient):
    run_id = await create_run(client)

    frames = await read_frames(client, run_id)

    assert len(frames) == 1
    frame = frames[0]
    assert frame["event"] == "research_started"
    assert frame["id"] == "1"
    assert frame["data"]["run_id"] == run_id
    assert frame["data"]["status"] == "queued"
    assert frame["data"]["payload"]["mode"] == "deep"


async def test_every_frame_carries_the_run_status(client: AsyncClient):
    """The header stays correct without a second request."""
    run_id = await create_run(client)

    frame = (await read_frames(client, run_id))[0]

    assert frame["data"]["status"] == "queued"
    assert frame["data"]["seq"] == 1


async def test_last_event_id_resumes_instead_of_replaying(client: AsyncClient):
    """A reconnecting client must not be re-sent what it already has."""
    run_id = await create_run(client)
    await client.post(f"{API}/research/{run_id}/cancel")

    # seq 1 is research_started, seq 2 is research_cancelled.
    all_frames = await read_frames(client, run_id, limit=2)
    assert [frame["event"] for frame in all_frames] == [
        "research_started",
        "research_cancelled",
    ]

    resumed = await read_frames(client, run_id, headers={"Last-Event-ID": "1"})
    assert [frame["event"] for frame in resumed] == ["research_cancelled"]


async def test_a_malformed_last_event_id_replays_from_the_start(client: AsyncClient):
    """A bad header must not break the stream; it degrades to a full replay."""
    run_id = await create_run(client)

    frames = await read_frames(client, run_id, headers={"Last-Event-ID": "not-a-number"})

    assert frames[0]["event"] == "research_started"


async def test_the_stream_closes_after_a_terminal_event(client: AsyncClient):
    """A finished run has nothing left to send; holding the socket is a leak."""
    run_id = await create_run(client)
    await client.post(f"{API}/research/{run_id}/cancel")

    async with asyncio.timeout(10):
        async with client.stream("GET", f"{API}/research/{run_id}/events") as response:
            body = "".join([chunk async for chunk in response.aiter_text()])

    assert "event: research_cancelled" in body
    # The stream ended on its own rather than being cut off by the timeout.
    assert body.count("event: research_started") == 1


async def test_ownership_is_checked_before_any_byte_is_streamed(client: AsyncClient):
    run_id = await create_run(client)
    other = uuid4()

    response = await client.get(f"{API}/research/{run_id}/events", headers=as_user(other))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"


async def test_streaming_an_unknown_run_is_a_404(client: AsyncClient):
    response = await client.get(f"{API}/research/{uuid4()}/events")
    assert response.status_code == 404


async def test_the_stream_sets_no_transform_and_no_buffering_headers(client: AsyncClient):
    """Proxies buffer streamed responses unless explicitly told not to."""
    run_id = await create_run(client)
    await client.post(f"{API}/research/{run_id}/cancel")

    async with asyncio.timeout(10):
        async with client.stream("GET", f"{API}/research/{run_id}/events") as response:
            assert "no-transform" in response.headers["cache-control"]
            assert response.headers["x-accel-buffering"] == "no"


async def test_an_open_stream_closes_at_the_connection_ceiling(client: AsyncClient):
    """A stream is not held open forever.

    The browser reconnects with Last-Event-ID and resumes exactly where it
    stopped, so a bounded connection costs nothing and caps the number of
    sockets one API process can accumulate.
    """
    run_id = await create_run(client)

    async with asyncio.timeout(10):
        async with client.stream("GET", f"{API}/research/{run_id}/events") as response:
            body = "".join([chunk async for chunk in response.aiter_text()])

    # It ended on its own, having sent the replay and then heartbeats.
    assert "event: research_started" in body
    assert ": heartbeat" in body


# --- what the worker publishes (Phase 14) ---------------------------------


async def test_an_event_published_by_another_process_reaches_the_client(
    client: AsyncClient, settings: Settings, database: Database
):
    """The worker is not the process holding this connection.

    In the test environment the transport is in-process, so a second broker's
    subscribers are not this app's - which is exactly the case the durable log
    covers: the event is replayed from rows the other process wrote.
    """
    run_id = await create_run(client)
    elsewhere = build_event_broker(settings, database=database)
    await elsewhere.publish(
        EventDraft(
            type=ResearchEventType.SOURCE_FOUND,
            run_id=UUID(run_id),
            status=RunStatus.RESEARCHING,
            payload={"source_id": str(uuid4()), "title": "A page a worker read"},
        )
    )

    frames = await read_frames(client, run_id, limit=2)

    assert [frame["event"] for frame in frames] == ["research_started", "source_found"]
    assert [frame["id"] for frame in frames] == ["1", "2"]
    assert frames[1]["data"]["payload"]["title"] == "A page a worker read"


async def test_a_reconnect_after_a_worker_event_resumes_from_it(
    client: AsyncClient, settings: Settings, database: Database
):
    """`Last-Event-ID` is answered from the log, not from this process's memory."""
    run_id = await create_run(client)
    elsewhere = build_event_broker(settings, database=database)
    for index in range(3):
        await elsewhere.publish(
            EventDraft(
                type=ResearchEventType.CLAIM_EXTRACTED,
                run_id=UUID(run_id),
                status=RunStatus.RESEARCHING,
                payload={"claim_id": str(uuid4()), "text": f"Claim {index}"},
            )
        )

    resumed = await read_frames(client, run_id, limit=2, headers={"Last-Event-ID": "2"})

    assert [frame["id"] for frame in resumed] == ["3", "4"]
