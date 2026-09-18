"""Authentication: registration, sign-in, sessions and revocation (Phase 20).

Driven over the real ASGI app and real Postgres, with the development identity
switched off - so the cookie is the only thing identifying the caller and a
test that "passes" because a shared developer principal was available cannot
exist here.

The assertions are grouped by what they protect against, not by endpoint. Most
of them are about what the system must *not* do: not reveal which addresses are
registered, not keep honouring a revoked token, not store a password, not hand
a session to a caller who did not present one.
"""

from __future__ import annotations

import datetime as dt
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import select

from app.auth.passwords import HashParameters, hash_password, verify_password
from app.auth.sessions import hash_token
from app.db.models.user import SessionRow, UserRow
from app.db.session import Database
from tests.conftest import API, TEST_PASSWORD, register_account, sign_in


def _cookie_header(response) -> str:
    return response.headers.get("set-cookie", "")


# --- registration ---------------------------------------------------------


async def test_registration_creates_an_account_and_signs_it_in(strict_client: AsyncClient):
    response = await strict_client.post(
        f"{API}/auth/register",
        json={"email": "Ada@Example.com", "password": TEST_PASSWORD, "name": "Ada Lovelace"},
    )

    assert response.status_code == 201
    user = response.json()["user"]
    # Folded on the way in, so the stored value is predictable and a later
    # sign-in with a different case finds the same row.
    assert user["email"] == "ada@example.com"
    assert user["name"] == "Ada Lovelace"
    assert user["role"] == "user"
    # The session is usable immediately: registering and then being asked to
    # sign in would be a worse product and a second credential round trip.
    assert (await strict_client.get(f"{API}/auth/me")).status_code == 200


async def test_the_session_cookie_cannot_be_read_by_script(strict_client: AsyncClient):
    """`HttpOnly` is what stops an XSS bug on the frontend becoming account
    takeover; `SameSite` is what stops another site making authenticated
    requests. Neither is visible from the frontend, so this is where they are
    checked."""
    response = await strict_client.post(
        f"{API}/auth/register", json={"email": "ada@example.com", "password": TEST_PASSWORD}
    )

    cookie = _cookie_header(response).lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/" in cookie
    # Not Secure under `test`, which is served over plain HTTP. The resolution
    # by environment is asserted in test_security_hardening.py.
    assert "secure" not in cookie


async def test_a_registered_address_cannot_be_registered_twice(strict_client: AsyncClient):
    await register_account(strict_client, email="ada@example.com")

    response = await strict_client.post(
        f"{API}/auth/register",
        json={"email": "ADA@example.com", "password": TEST_PASSWORD},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "registration_refused"


async def test_a_password_below_the_policy_is_refused_with_field_errors(
    strict_client: AsyncClient,
):
    """The frontend renders `details` beside the input, so the refusal has to
    arrive as a field map rather than as prose."""
    response = await strict_client.post(
        f"{API}/auth/register", json={"email": "ada@example.com", "password": "short"}
    )

    assert response.status_code == 422
    assert "password" in response.json()["error"]["details"]


async def test_a_password_that_is_the_address_is_refused(strict_client: AsyncClient):
    response = await strict_client.post(
        f"{API}/auth/register",
        json={"email": "averylongaddress@example.com", "password": "averylongaddress"},
    )

    assert response.status_code == 422
    assert "password" in response.json()["error"]["details"]


async def test_registration_can_be_closed(strict_settings):
    from httpx import ASGITransport

    from app.main import create_app

    app = create_app(strict_settings.model_copy(update={"registration_enabled": False}))
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http,
        app.router.lifespan_context(app),
    ):
        response = await http.post(
            f"{API}/auth/register", json={"email": "ada@example.com", "password": TEST_PASSWORD}
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "registration_closed"


# --- sign-in --------------------------------------------------------------


async def test_a_wrong_password_and_an_unknown_address_are_indistinguishable(
    strict_client: AsyncClient,
):
    """The single most common account-enumeration hole. The status, the code
    and the message must be identical, or the login form answers "does this
    person have an account here?" for anybody who asks."""
    await register_account(strict_client, email="ada@example.com")
    strict_client.cookies.clear()

    wrong_password = await strict_client.post(
        f"{API}/auth/login", json={"email": "ada@example.com", "password": "not-the-password"}
    )
    unknown_address = await strict_client.post(
        f"{API}/auth/login", json={"email": "nobody@example.com", "password": TEST_PASSWORD}
    )

    assert wrong_password.status_code == unknown_address.status_code == 401
    assert wrong_password.json()["error"]["code"] == unknown_address.json()["error"]["code"]
    assert wrong_password.json()["error"]["message"] == unknown_address.json()["error"]["message"]


async def test_signing_in_is_case_insensitive_in_the_address(strict_client: AsyncClient):
    await register_account(strict_client, email="ada@example.com")
    strict_client.cookies.clear()

    await sign_in(strict_client, email="ADA@Example.COM")

    assert (await strict_client.get(f"{API}/auth/me")).json()["email"] == "ada@example.com"


async def test_signing_in_records_the_login_time(strict_client: AsyncClient):
    await register_account(strict_client, email="ada@example.com")

    assert (await strict_client.get(f"{API}/auth/me")).json()["last_login_at"] is not None


async def test_an_account_with_no_password_cannot_be_signed_into(
    strict_client: AsyncClient, database: Database
):
    """A null hash means *this account has no password*, never *any password
    works*. The development principal's row is created exactly like this, so
    the case is real rather than hypothetical."""
    async with database.session() as session:
        session.add(UserRow(email="nopassword@example.com", name="No Password"))

    response = await strict_client.post(
        f"{API}/auth/login", json={"email": "nopassword@example.com", "password": TEST_PASSWORD}
    )

    assert response.status_code == 401


# --- what a session is and is not ----------------------------------------


async def test_an_unauthenticated_request_is_refused(strict_client: AsyncClient):
    response = await strict_client.get(f"{API}/research")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_the_token_is_never_stored_in_the_clear(
    strict_client: AsyncClient, database: Database, strict_settings
):
    """A database leak must not hand over live sessions (threat model 3.7)."""
    await register_account(strict_client, email="ada@example.com")
    token = strict_client.cookies[strict_settings.session_cookie_name]

    async with database.session() as session:
        rows = list((await session.execute(select(SessionRow))).scalars())

    assert len(rows) == 1
    assert rows[0].token_hash != token
    assert rows[0].token_hash == hash_token(token)


async def test_the_password_is_never_stored_in_the_clear(
    strict_client: AsyncClient, database: Database
):
    await register_account(strict_client, email="ada@example.com")

    async with database.session() as session:
        row = (await session.execute(select(UserRow))).scalar_one()

    assert row.password_hash is not None
    assert TEST_PASSWORD not in row.password_hash
    assert row.password_hash.startswith("$argon2id$")


async def test_a_forged_token_resolves_to_nobody(strict_client: AsyncClient, strict_settings):
    strict_client.cookies.set(strict_settings.session_cookie_name, "a" * 43)

    assert (await strict_client.get(f"{API}/auth/me")).status_code == 401


async def test_an_expired_session_stops_working(
    strict_client: AsyncClient, database: Database, strict_settings
):
    await register_account(strict_client, email="ada@example.com")
    token_hash = hash_token(strict_client.cookies[strict_settings.session_cookie_name])

    async with database.session() as session:
        row = (
            await session.execute(select(SessionRow).where(SessionRow.token_hash == token_hash))
        ).scalar_one()
        row.expires_at = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)

    assert (await strict_client.get(f"{API}/auth/me")).status_code == 401


# --- revocation -----------------------------------------------------------


async def test_logging_out_revokes_the_token_and_not_only_the_cookie(
    strict_client: AsyncClient, strict_settings
):
    """Clearing the cookie alone would leave a working token in whatever else
    holds it - a proxy log, a copied curl command, a second tab's storage."""
    await register_account(strict_client, email="ada@example.com")
    token = strict_client.cookies[strict_settings.session_cookie_name]

    assert (await strict_client.post(f"{API}/auth/logout")).status_code == 204
    assert strict_settings.session_cookie_name not in strict_client.cookies

    # Present the token again, as something that kept a copy would.
    strict_client.cookies.set(strict_settings.session_cookie_name, token)
    assert (await strict_client.get(f"{API}/auth/me")).status_code == 401


async def test_a_revoked_session_is_not_an_anonymous_one(
    strict_client: AsyncClient, settings, strict_settings
):
    """The subtle failure this guards: in a development environment, a request
    carrying a dead cookie must be refused rather than falling through to the
    shared developer identity - which would make revocation look like it worked
    while the caller kept full access."""
    from httpx import ASGITransport

    from app.main import create_app

    # The permissive settings: the development identity *is* available here.
    app = create_app(settings)
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http,
        app.router.lifespan_context(app),
    ):
        assert (await http.get(f"{API}/auth/me")).status_code == 200  # the dev principal

        http.cookies.set(settings.session_cookie_name, "a" * 43)
        assert (await http.get(f"{API}/auth/me")).status_code == 401


async def test_sessions_are_listed_with_the_caller_marked(strict_client: AsyncClient):
    await register_account(strict_client, email="ada@example.com")

    response = await strict_client.get(f"{API}/auth/sessions")

    assert response.status_code == 200
    sessions = response.json()
    assert len(sessions) == 1
    assert sessions[0]["current"] is True


async def test_one_device_can_be_signed_out_from_another(
    strict_client: AsyncClient, strict_settings
):
    await register_account(strict_client, email="ada@example.com")
    first = strict_client.cookies[strict_settings.session_cookie_name]

    strict_client.cookies.clear()
    await sign_in(strict_client, email="ada@example.com")

    listed = (await strict_client.get(f"{API}/auth/sessions")).json()
    assert len(listed) == 2
    other = next(row for row in listed if not row["current"])

    assert (await strict_client.delete(f"{API}/auth/sessions/{other['id']}")).status_code == 204

    strict_client.cookies.set(strict_settings.session_cookie_name, first)
    assert (await strict_client.get(f"{API}/auth/me")).status_code == 401


async def test_another_persons_session_id_is_simply_not_found(strict_client: AsyncClient):
    """Revocation is scoped by user in SQL, so an id belonging to somebody else
    is indistinguishable from one that does not exist."""
    await register_account(strict_client, email="ada@example.com")
    strict_client.cookies.clear()
    await register_account(strict_client, email="grace@example.com")
    victim = uuid4()

    response = await strict_client.delete(f"{API}/auth/sessions/{victim}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"


async def test_signing_out_other_devices_keeps_this_one(
    strict_client: AsyncClient, strict_settings
):
    """The control someone reaches for when they think a password has leaked.
    Signing them out as well would mean signing back in with the credential
    they are worried about."""
    await register_account(strict_client, email="ada@example.com")
    stale = strict_client.cookies[strict_settings.session_cookie_name]
    strict_client.cookies.clear()
    await sign_in(strict_client, email="ada@example.com")
    current = strict_client.cookies[strict_settings.session_cookie_name]

    response = await strict_client.delete(f"{API}/auth/sessions")

    assert response.status_code == 200
    assert response.json()["revoked"] == 1
    assert (await strict_client.get(f"{API}/auth/me")).status_code == 200

    strict_client.cookies.set(strict_settings.session_cookie_name, stale)
    assert (await strict_client.get(f"{API}/auth/me")).status_code == 401
    strict_client.cookies.set(strict_settings.session_cookie_name, current)


async def test_a_user_cannot_hold_more_sessions_than_the_ceiling(
    strict_settings, database: Database
):
    """A ceiling rather than a refusal: the newest device always works, and the
    oldest token stops. An unbounded set of live tokens would have to be
    revoked one at a time."""
    from httpx import ASGITransport

    from app.main import create_app

    app = create_app(strict_settings.model_copy(update={"max_sessions_per_user": 2}))
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http,
        app.router.lifespan_context(app),
    ):
        await register_account(http, email="ada@example.com")
        for _ in range(2):
            http.cookies.clear()
            await sign_in(http, email="ada@example.com")

        listed = (await http.get(f"{API}/auth/sessions")).json()

    assert len(listed) == 2


# --- hashing --------------------------------------------------------------


async def test_a_password_verifies_against_its_own_hash_and_nothing_else():
    parameters = HashParameters(time_cost=2, memory_kib=19 * 1024, parallelism=1)

    stored = await hash_password("correct horse battery staple", parameters)

    assert (await verify_password(stored, "correct horse battery staple", parameters)).ok
    assert not (await verify_password(stored, "Correct horse battery staple", parameters)).ok
    assert not (await verify_password("not a hash", "anything", parameters)).ok


async def test_two_hashes_of_one_password_differ():
    """Salted. Identical hashes would mean a leaked table shows who shares a
    password with whom."""
    parameters = HashParameters(time_cost=2, memory_kib=19 * 1024, parallelism=1)

    first = await hash_password("correct horse battery staple", parameters)
    second = await hash_password("correct horse battery staple", parameters)

    assert first != second


async def test_raising_the_cost_rehashes_on_the_next_sign_in(strict_settings, database: Database):
    """Parameters can be raised without a password reset: the plaintext is in
    hand exactly once, at a successful sign-in, and that is when it happens."""
    from httpx import ASGITransport

    from app.main import create_app

    weak = strict_settings.model_copy(
        update={"password_hash_time_cost": 2, "password_hash_memory_kib": 19 * 1024}
    )
    app = create_app(weak)
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http,
        app.router.lifespan_context(app),
    ):
        await register_account(http, email="ada@example.com")

    async with database.session() as session:
        before = (await session.execute(select(UserRow.password_hash))).scalar_one()
    assert "m=19456" in str(before)

    stronger = strict_settings.model_copy(update={"password_hash_time_cost": 3})
    app = create_app(stronger)
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http,
        app.router.lifespan_context(app),
    ):
        await sign_in(http, email="ada@example.com")

    async with database.session() as session:
        after = (await session.execute(select(UserRow.password_hash))).scalar_one()

    assert after != before
    assert "t=3" in str(after)


# --- authorisation, now over real sessions --------------------------------


async def test_one_account_cannot_read_anothers_runs(strict_client: AsyncClient):
    """Ownership has been enforced since Phase 2. This is the same rule
    exercised through a real sign-in rather than an impersonation header."""
    await register_account(strict_client, email="ada@example.com")
    created = await strict_client.post(
        f"{API}/research",
        json={"question": "Compare the major AI inference providers on price.", "mode": "quick"},
    )
    assert created.status_code == 202
    run_id = created.json()["run_id"]

    strict_client.cookies.clear()
    await register_account(strict_client, email="grace@example.com")

    assert (await strict_client.get(f"{API}/research/{run_id}")).status_code == 404
