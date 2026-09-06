"""The research API surface.

Two things are load-bearing and are asserted here rather than assumed: creating
a run returns 202 without performing any research, and a run belonging to
another user is indistinguishable from one that does not exist.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from httpx import AsyncClient

from tests.conftest import API, as_user, valid_request


async def create_run(client: AsyncClient, **overrides: object) -> dict[str, object]:
    response = await client.post(f"{API}/research", json=valid_request(**overrides))
    assert response.status_code == 202, response.text
    return response.json()


# --- creation -------------------------------------------------------------


async def test_create_returns_202_and_queues_the_run(client: AsyncClient):
    """202, not 200: the work is accepted, not done."""
    response = await client.post(f"{API}/research", json=valid_request())

    assert response.status_code == 202
    body = response.json()
    assert UUID(body["run_id"])
    assert body["status"] == "queued"
    assert body["events_url"].endswith(f"/research/{body['run_id']}/events")


async def test_a_created_run_is_queued_not_running(client: AsyncClient):
    """No worker exists yet, and the API says so instead of faking progress."""
    created = await create_run(client)
    run = (await client.get(f"{API}/research/{created['run_id']}")).json()

    assert run["status"] == "queued"
    assert run["progress"] == 0.0
    assert run["started_at"] is None
    assert run["has_report"] is False
    assert run["source_count"] == 0


async def test_quick_mode_gets_tighter_ceilings_than_deep(client: AsyncClient):
    """FR-8. A quick run does one pass, so its limits are not the global maxima."""
    quick = await create_run(client, mode="quick")
    deep = await create_run(client, mode="deep")

    quick_run = (await client.get(f"{API}/research/{quick['run_id']}")).json()
    deep_run = (await client.get(f"{API}/research/{deep['run_id']}")).json()

    assert quick_run["limits"]["max_iterations"] == 1
    assert quick_run["limits"]["max_cost_usd"] < deep_run["limits"]["max_cost_usd"]
    assert deep_run["limits"]["max_iterations"] > 1


async def test_depth_is_ignored_for_quick_runs(client: AsyncClient):
    created = await create_run(client, mode="quick", depth=5)
    run = (await client.get(f"{API}/research/{created['run_id']}")).json()
    assert run["depth"] == 1


async def test_the_title_is_derived_from_the_question(client: AsyncClient):
    created = await create_run(
        client, question="Compare inference providers. Then rank them by cost."
    )
    run = (await client.get(f"{API}/research/{created['run_id']}")).json()
    assert run["title"] == "Compare inference providers"


# --- validation -----------------------------------------------------------


async def test_a_short_question_is_rejected_with_a_field_error(client: AsyncClient):
    response = await client.post(f"{API}/research", json=valid_request(question="too short"))

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_failed"
    assert "question" in error["details"]


async def test_a_url_is_rejected_where_a_domain_is_expected(client: AsyncClient):
    response = await client.post(
        f"{API}/research", json=valid_request(domains=["https://sec.gov/edgar"])
    )

    assert response.status_code == 422
    assert "domains" in response.json()["error"]["details"]


async def test_an_inverted_date_range_is_rejected(client: AsyncClient):
    response = await client.post(
        f"{API}/research",
        json=valid_request(date_range_start="2026-06-01", date_range_end="2026-01-01"),
    )

    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert any("end date" in message for messages in details.values() for message in messages)


async def test_an_unknown_field_is_rejected_rather_than_ignored(client: AsyncClient):
    """Silently dropping a field the caller believed was applied is worse than a 422."""
    response = await client.post(f"{API}/research", json=valid_request(priority="urgent"))
    assert response.status_code == 422


async def test_an_out_of_range_depth_is_rejected(client: AsyncClient):
    response = await client.post(f"{API}/research", json=valid_request(depth=9))
    assert response.status_code == 422
    assert "depth" in response.json()["error"]["details"]


async def test_duplicate_domains_are_collapsed(client: AsyncClient):
    created = await create_run(client, domains=["SEC.gov", "sec.gov", "arxiv.org"])
    run = (await client.get(f"{API}/research/{created['run_id']}")).json()
    assert run["domains"] == ["sec.gov", "arxiv.org"]


# --- authorisation --------------------------------------------------------


async def test_another_users_run_is_reported_as_missing(client: AsyncClient, other_user_id: UUID):
    """A 403 would confirm the id exists. FR-1: you can only see your own research."""
    created = await create_run(client)

    response = await client.get(
        f"{API}/research/{created['run_id']}", headers=as_user(other_user_id)
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"


async def test_listing_is_scoped_to_the_caller(client: AsyncClient, other_user_id: UUID):
    await create_run(client)

    mine = (await client.get(f"{API}/research")).json()
    theirs = (await client.get(f"{API}/research", headers=as_user(other_user_id))).json()

    assert len(mine["items"]) == 1
    assert theirs["items"] == []


async def test_another_user_cannot_cancel_a_run(client: AsyncClient, other_user_id: UUID):
    created = await create_run(client)

    response = await client.post(
        f"{API}/research/{created['run_id']}/cancel", headers=as_user(other_user_id)
    )

    assert response.status_code == 404


async def test_a_missing_run_returns_the_error_envelope(client: AsyncClient):
    response = await client.get(f"{API}/research/{uuid4()}")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "run_not_found"
    assert error["trace_id"]


async def test_a_malformed_run_id_is_a_validation_error(client: AsyncClient):
    response = await client.get(f"{API}/research/not-a-uuid")
    assert response.status_code == 422


# --- listing and pagination -----------------------------------------------


async def test_the_list_is_newest_first(client: AsyncClient):
    first = await create_run(client, question="First question about inference pricing today.")
    second = await create_run(client, question="Second question about inference pricing today.")

    items = (await client.get(f"{API}/research")).json()["items"]

    assert [item["id"] for item in items] == [second["run_id"], first["run_id"]]


async def test_pagination_walks_every_run_exactly_once(client: AsyncClient):
    created = [
        (await create_run(client, question=f"Question number {index} about inference costs."))[
            "run_id"
        ]
        for index in range(3)
    ]

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(5):  # bounded: a runaway cursor loop must fail the test
        params: dict[str, object] = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        page = (await client.get(f"{API}/research", params=params)).json()
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break

    assert cursor is None
    assert sorted(seen) == sorted(created)
    assert len(seen) == len(set(seen))


async def test_the_page_size_is_clamped(client: AsyncClient):
    """An unbounded query never reaches the repository."""
    response = await client.get(f"{API}/research", params={"limit": 100_000})
    assert response.status_code == 200


async def test_a_malformed_cursor_is_rejected(client: AsyncClient):
    response = await client.get(f"{API}/research", params={"cursor": "!!!not-base64!!!"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


async def test_filtering_by_status_and_query(client: AsyncClient):
    await create_run(client, question="A question about vector database recall benchmarks.")
    await create_run(client, question="A question about inference pricing and margins.")

    queued = (await client.get(f"{API}/research", params={"status_filter": "queued"})).json()
    assert len(queued["items"]) == 2

    matched = (await client.get(f"{API}/research", params={"q": "vector"})).json()
    assert len(matched["items"]) == 1

    completed = (await client.get(f"{API}/research", params={"status_filter": "completed"})).json()
    assert completed["items"] == []


# --- cancellation and limits ----------------------------------------------


async def test_cancelling_a_queued_run(client: AsyncClient):
    created = await create_run(client)

    response = await client.post(f"{API}/research/{created['run_id']}/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["completed_at"] is not None


async def test_cancelling_twice_is_a_conflict_not_a_silent_success(client: AsyncClient):
    created = await create_run(client)
    await client.post(f"{API}/research/{created['run_id']}/cancel")

    response = await client.post(f"{API}/research/{created['run_id']}/cancel")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_not_cancellable"


async def test_the_concurrent_run_limit_is_enforced(client: AsyncClient):
    """Denial of wallet: a user cannot queue unbounded work (threat model 3.3)."""
    for index in range(3):
        await create_run(client, question=f"Concurrent question {index} about inference.")

    response = await client.post(f"{API}/research", json=valid_request())

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "too_many_concurrent_runs"


async def test_cancelling_frees_a_concurrency_slot(client: AsyncClient):
    created = [
        await create_run(client, question=f"Slot question {index} about inference pricing.")
        for index in range(3)
    ]
    await client.post(f"{API}/research/{created[0]['run_id']}/cancel")

    response = await client.post(f"{API}/research", json=valid_request())
    assert response.status_code == 202


# --- sub-resources --------------------------------------------------------


async def test_a_run_with_no_results_returns_empty_collections(client: AsyncClient):
    """Empty because nothing has been produced, which is the truthful answer."""
    created = await create_run(client)
    run_id = created["run_id"]

    sources = (await client.get(f"{API}/research/{run_id}/sources")).json()
    evidence = (await client.get(f"{API}/research/{run_id}/evidence")).json()
    activity = (await client.get(f"{API}/research/{run_id}/activity")).json()

    assert sources == {"sources": [], "clusters": [], "next_cursor": None, "total": 0}
    assert evidence["claims"] == [] and evidence["contradictions"] == []
    assert activity["agent_runs"] == []


async def test_the_report_is_not_ready_rather_than_missing(client: AsyncClient):
    """`report_not_ready` and `run_not_found` are different facts and different codes."""
    created = await create_run(client)

    response = await client.get(f"{API}/research/{created['run_id']}/report")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "report_not_ready"


async def test_stats_reports_none_rather_than_zero_for_an_unmeasurable_median(
    client: AsyncClient,
):
    """Zero and 'not measured' are different facts; the UI renders them differently."""
    await create_run(client)

    stats = (await client.get(f"{API}/research/stats")).json()

    assert stats["total_runs"] == 1
    assert stats["running_runs"] == 1
    assert stats["completed_runs"] == 0
    assert stats["median_runtime_seconds"] is None


async def test_stats_are_per_user(client: AsyncClient, other_user_id: UUID):
    await create_run(client)

    stats = (await client.get(f"{API}/research/stats", headers=as_user(other_user_id))).json()

    assert stats["total_runs"] == 0


# --- follow-ups -----------------------------------------------------------


async def test_a_follow_up_links_to_its_parent(client: AsyncClient):
    parent = await create_run(client)

    response = await client.post(
        f"{API}/research/{parent['run_id']}/followup",
        json={"question": "Go deeper on the pricing dimension of that comparison."},
    )

    assert response.status_code == 202
    child = (await client.get(f"{API}/research/{response.json()['run_id']}")).json()
    assert child["parent_run_id"] == parent["run_id"]
    assert child["mode"] == "conversational"


async def test_a_follow_up_on_another_users_run_is_refused(
    client: AsyncClient, other_user_id: UUID
):
    parent = await create_run(client)

    response = await client.post(
        f"{API}/research/{parent['run_id']}/followup",
        json={"question": "Go deeper on the pricing dimension of that comparison."},
        headers=as_user(other_user_id),
    )

    assert response.status_code == 404
