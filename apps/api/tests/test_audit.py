"""The audit log (Phase 20).

The threat model names an "audit log of auth and research mutations" as the
repudiation control. What makes it a control rather than a table is the set of
properties below, and every one of them is a way it could quietly stop working:
a refused login that leaves no trace, a row rolled back with the request that
produced it, a secret written into the context blob, a trail deleted along with
the account it belonged to.
"""

from __future__ import annotations

import datetime as dt
import uuid

from httpx import AsyncClient
from sqlalchemy import delete, select

from app.core.enums import AuditAction, AuditOutcome
from app.db.models.audit import AuditLogRow
from app.db.models.user import UserRow
from app.db.repositories.audit import SqlAlchemyAuditLog
from app.db.session import Database
from tests.conftest import API, TEST_PASSWORD, register_account

#: A value that must not survive into a row. Named rather than inlined so
#: the linter does not read the assertion as a hardcoded credential.
_SECRET = "a-real-token"  # noqa: S105


async def _rows(database: Database, action: AuditAction | None = None) -> list[AuditLogRow]:
    statement = select(AuditLogRow).order_by(AuditLogRow.created_at)
    if action is not None:
        statement = statement.where(AuditLogRow.action == str(action))
    async with database.session() as session:
        rows = list((await session.execute(statement)).scalars())
        for row in rows:
            session.expunge(row)
        return rows


async def test_a_successful_login_is_recorded_with_who_and_from_where(
    strict_client: AsyncClient, database: Database
):
    await register_account(strict_client, email="ada@example.com")
    strict_client.cookies.clear()
    await strict_client.post(
        f"{API}/auth/login",
        json={"email": "ada@example.com", "password": TEST_PASSWORD},
        headers={"user-agent": "Mozilla/5.0 (a test)"},
    )

    rows = await _rows(database, AuditAction.LOGIN)

    assert len(rows) == 1
    assert rows[0].outcome == AuditOutcome.SUCCESS
    assert rows[0].user_id is not None
    assert rows[0].user_agent == "Mozilla/5.0 (a test)"
    # The request id echoed to the caller, so a row can be joined to the logs
    # of the request that produced it.
    assert rows[0].request_id


async def test_a_refused_login_is_recorded_although_the_request_rolled_back(
    strict_client: AsyncClient, database: Database
):
    """The reason the audit store has a transaction of its own. A refusal
    raises `Unauthenticated`, which rolls the request's transaction back - an
    audit row written into it would be rolled back too, and the log would
    contain successful logins only, which is the half nobody investigates."""
    await strict_client.post(
        f"{API}/auth/login", json={"email": "nobody@example.com", "password": TEST_PASSWORD}
    )

    rows = await _rows(database, AuditAction.LOGIN_FAILED)

    assert len(rows) == 1
    assert rows[0].outcome == AuditOutcome.FAILURE
    assert rows[0].context["email"] == "nobody@example.com"
    assert rows[0].context["reason"] == "invalid_credentials"


async def test_the_context_never_carries_the_password(
    strict_client: AsyncClient, database: Database
):
    """A row that records an attempt must not record the guess. This is
    structural - the endpoint never passes it - and asserted anyway, because
    the cost of finding out otherwise is a table of plaintext passwords."""
    await strict_client.post(
        f"{API}/auth/login",
        json={"email": "nobody@example.com", "password": "hunter2-is-the-secret"},
    )

    rows = await _rows(database)

    assert rows
    assert "hunter2-is-the-secret" not in str([row.context for row in rows])


async def test_research_mutations_are_recorded_and_reads_are_not(
    strict_client: AsyncClient, database: Database
):
    """An audit trail that records everything records nothing: every request is
    already in the access log, and the rows worth keeping are the ones that
    changed what a person owns."""
    await register_account(strict_client, email="ada@example.com")
    created = await strict_client.post(
        f"{API}/research",
        json={"question": "Compare the major AI inference providers on price.", "mode": "quick"},
    )
    run_id = created.json()["run_id"]
    await strict_client.get(f"{API}/research/{run_id}")
    await strict_client.post(f"{API}/research/{run_id}/cancel")

    actions = [row.action for row in await _rows(database)]

    assert actions == [
        AuditAction.REGISTER,
        AuditAction.RUN_CREATED,
        AuditAction.RUN_CANCELLED,
    ]


async def test_a_run_row_names_the_run_it_is_about(strict_client: AsyncClient, database: Database):
    await register_account(strict_client, email="ada@example.com")
    created = await strict_client.post(
        f"{API}/research",
        json={"question": "Compare the major AI inference providers on price.", "mode": "quick"},
    )

    rows = await _rows(database, AuditAction.RUN_CREATED)

    assert len(rows) == 1
    assert rows[0].resource_type == "research_run"
    assert str(rows[0].resource_id) == created.json()["run_id"]


async def test_deleting_an_account_keeps_its_trail_and_its_actor(
    strict_client: AsyncClient, database: Database
):
    """No foreign key, so no cascade and no nulling. Deleting the evidence
    along with the account is what an audit log must never do, and anonymising
    it is only slightly better: "who" is the question being asked."""
    user = await register_account(strict_client, email="ada@example.com")
    async with database.session() as session:
        await session.execute(delete(UserRow))

    rows = await _rows(database)

    assert rows
    assert all(str(row.user_id) == user["id"] for row in rows)


async def test_a_successful_registration_is_recorded(
    strict_client: AsyncClient, database: Database
):
    """The case the foreign key broke. Registration writes the user in the
    request's transaction and the audit row in its own, so at the moment the
    row is inserted the user it names is not committed yet - with a key in
    place the insert was refused, swallowed and logged, and every sign-up went
    unrecorded."""
    await register_account(strict_client, email="ada@example.com")

    rows = await _rows(database, AuditAction.REGISTER)

    assert len(rows) == 1
    assert rows[0].user_id is not None


async def test_a_write_that_fails_does_not_fail_the_request(database: Database, monkeypatch):
    """The other half of the trade the threat model records: failing closed
    would mean a database hiccup denies every login in the deployment."""
    log = SqlAlchemyAuditLog(database)

    def explode(*_: object, **__: object) -> None:
        raise RuntimeError("the audit table is unavailable")

    monkeypatch.setattr(database, "session", explode)

    # No exception: the caller is a login that has already succeeded.
    await log.record(action=AuditAction.LOGIN, outcome=AuditOutcome.SUCCESS)


async def test_context_values_are_bounded_and_redacted(database: Database):
    """The blob is for a reason code or a count. A value that arrived from a
    request must not be able to make the row arbitrarily large, and a key that
    names a secret is redacted by the same rule the structured logger uses."""
    log = SqlAlchemyAuditLog(database)

    await log.record(
        action=AuditAction.LOGIN,
        outcome=AuditOutcome.SUCCESS,
        context={"note": "x" * 5000, "session_token": _SECRET, "count": 3},
    )

    rows = await _rows(database)

    assert len(rows[0].context["note"]) == 200
    assert rows[0].context["session_token"] != _SECRET
    assert rows[0].context["count"] == 3


async def test_one_accounts_trail_can_be_read_back(strict_client: AsyncClient, database: Database):
    """The query an investigation opens with, and the index that serves it."""
    user = await register_account(strict_client, email="ada@example.com")
    log = SqlAlchemyAuditLog(database)

    rows = await log.recent_for_user(
        uuid.UUID(str(user["id"])),
        since=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5),
    )

    assert [row.action for row in rows] == [AuditAction.REGISTER]
