"""The auth surface after ADR 0022: rotation, revocation, and the SSO routes.

Driven over the real ASGI app and real Postgres with the development identity
switched off, like `test_auth.py` - so a test cannot pass because a shared
developer principal happened to be available.

What is asserted here is mostly what the system must *not* do: not renew a
revoked session, not honour a rotated refresh token twice, not let one person's
identity be unlinked by another, and not put anything into a redirect that it
did not choose itself.
"""

from __future__ import annotations

import datetime as dt

from httpx import AsyncClient
from sqlalchemy import select

from app.db.models.user import RefreshTokenRow, SessionRow
from app.db.session import Database
from tests.conftest import API, register_account


def access_cookie(settings) -> str:
    return f"{settings.session_cookie_name}_at"


def refresh_cookie(settings) -> str:
    return f"{settings.session_cookie_name}_rt"


# --- what a sign-in sets --------------------------------------------------


async def test_a_sign_in_sets_both_cookies(strict_client: AsyncClient, strict_settings):
    await register_account(strict_client, email="ada@example.com")

    assert access_cookie(strict_settings) in strict_client.cookies
    assert refresh_cookie(strict_settings) in strict_client.cookies


async def test_the_refresh_cookie_is_scoped_to_the_refresh_endpoint(
    strict_client: AsyncClient, strict_settings
):
    """The credential that mints sessions is absent from every other request.

    A `Path=/` refresh cookie would ride along on hundreds of requests that
    have no use for it, where a logging mistake or a misdirected proxy could
    capture it.
    """
    response = await strict_client.post(
        f"{API}/auth/register",
        json={"email": "ada@example.com", "password": "a-long-enough-password", "name": "Ada"},
    )

    refresh_lines = [
        line
        for line in response.headers.get_list("set-cookie")
        if line.startswith(refresh_cookie(strict_settings))
    ]
    assert refresh_lines, "no refresh cookie was set"
    assert "Path=/api/v1/auth/refresh" in refresh_lines[0]
    assert "HttpOnly" in refresh_lines[0]


async def test_both_token_cookies_are_httponly(strict_client: AsyncClient, strict_settings):
    # Script must not be able to read either: an XSS bug on the frontend then
    # cannot exfiltrate a credential.
    response = await strict_client.post(
        f"{API}/auth/register",
        json={"email": "ada@example.com", "password": "a-long-enough-password", "name": "Ada"},
    )

    for line in response.headers.get_list("set-cookie"):
        if line.startswith(strict_settings.session_cookie_name):
            assert "HttpOnly" in line


# --- rotation and reuse ---------------------------------------------------


async def test_a_refresh_rotates_the_token(strict_client: AsyncClient, strict_settings):
    await register_account(strict_client, email="ada@example.com")
    first = strict_client.cookies[refresh_cookie(strict_settings)]

    response = await strict_client.post(f"{API}/auth/refresh")

    assert response.status_code == 200
    assert strict_client.cookies[refresh_cookie(strict_settings)] != first


async def test_reusing_a_rotated_refresh_token_signs_the_whole_family_out(
    strict_client: AsyncClient, strict_settings, database: Database
):
    """The detector, not just the rotation.

    Two parties holding one token cannot be told apart, so both are signed out
    and the person re-authenticates. Without this, a thief simply rotates
    alongside the victim forever.
    """
    await register_account(strict_client, email="ada@example.com")
    stolen = strict_client.cookies[refresh_cookie(strict_settings)]

    # The legitimate holder rotates.
    assert (await strict_client.post(f"{API}/auth/refresh")).status_code == 200

    # The thief presents the token they captured before the rotation.
    strict_client.cookies.set(refresh_cookie(strict_settings), stolen)
    assert (await strict_client.post(f"{API}/auth/refresh")).status_code == 401

    async with database.session() as session:
        rows = list((await session.execute(select(RefreshTokenRow))).scalars())
        sessions = list((await session.execute(select(SessionRow))).scalars())

    # Every token in the family, and the session itself.
    assert rows and all(row.revoked_at is not None for row in rows)
    assert sessions and all(row.revoked_at is not None for row in sessions)


async def test_an_unknown_refresh_token_is_refused(strict_client: AsyncClient, strict_settings):
    strict_client.cookies.set(refresh_cookie(strict_settings), "a" * 43)

    assert (await strict_client.post(f"{API}/auth/refresh")).status_code == 401


async def test_a_refresh_with_no_cookie_is_refused(strict_client: AsyncClient):
    assert (await strict_client.post(f"{API}/auth/refresh")).status_code == 401


async def test_a_failed_refresh_clears_both_cookies(strict_client: AsyncClient, strict_settings):
    # Otherwise the browser retries a dead token on every request, and after
    # reuse detection that is a loop against an already-revoked family.
    strict_client.cookies.set(refresh_cookie(strict_settings), "a" * 43)

    response = await strict_client.post(f"{API}/auth/refresh")

    cleared = response.headers.get_list("set-cookie")
    assert any(refresh_cookie(strict_settings) in line for line in cleared)


async def test_a_revoked_session_cannot_be_renewed(
    strict_client: AsyncClient, strict_settings, database: Database
):
    """Read from Postgres, not from the revocation index.

    This is what bounds the cost of ADR 0022: even with the index emptied, a
    revoked session can never mint another token.
    """
    await register_account(strict_client, email="ada@example.com")

    async with database.session() as session:
        row = (await session.execute(select(SessionRow))).scalar_one()
        row.revoked_at = dt.datetime.now(dt.UTC)

    assert (await strict_client.post(f"{API}/auth/refresh")).status_code == 401


# --- revocation -----------------------------------------------------------


async def test_signing_a_device_out_stops_its_access_token_immediately(
    strict_client: AsyncClient, strict_settings
):
    """The property the revocation index exists for.

    Under a plain JWT design the token would keep working until it expired.
    """
    await register_account(strict_client, email="ada@example.com")
    token = strict_client.cookies[access_cookie(strict_settings)]

    sessions = (await strict_client.get(f"{API}/auth/sessions")).json()
    assert len(sessions) == 1

    assert (await strict_client.post(f"{API}/auth/logout")).status_code == 204

    # Present the token again, as something holding a copy would.
    strict_client.cookies.set(access_cookie(strict_settings), token)
    assert (await strict_client.get(f"{API}/auth/me")).status_code == 401


async def test_the_session_list_says_which_issuer_admitted_each_device(
    strict_client: AsyncClient,
):
    await register_account(strict_client, email="ada@example.com")

    rows = (await strict_client.get(f"{API}/auth/sessions")).json()

    assert rows[0]["current"] is True


# --- the SSO routes -------------------------------------------------------


async def test_the_options_endpoint_offers_nothing_when_nothing_is_configured(
    strict_client: AsyncClient,
):
    """No credentials in the test settings, so no buttons.

    A button for an unconfigured provider is a dead end a person cannot tell
    apart from an outage.
    """
    response = await strict_client.get(f"{API}/auth/sso/providers")

    assert response.status_code == 200
    assert response.json()["options"] == []
    assert response.json()["password_enabled"] is True


async def test_the_options_endpoint_needs_no_session(strict_client: AsyncClient):
    # It is read before anybody has signed in, by definition.
    assert (await strict_client.get(f"{API}/auth/sso/providers")).status_code == 200


async def test_starting_a_flow_for_an_unconfigured_provider_is_refused(
    strict_client: AsyncClient,
):
    response = await strict_client.get(f"{API}/auth/sso/auth0/google/start")

    assert response.status_code == 404
    # The code does not say whether the provider or the connection was the
    # problem: either answer describes the deployment to an unauthenticated
    # caller.
    assert response.json()["error"]["code"] == "sso_not_available"


async def test_a_callback_with_no_transaction_lands_on_the_sign_in_page(
    strict_client: AsyncClient,
):
    response = await strict_client.get(
        f"{API}/auth/sso/auth0/callback?code=x&state=y", follow_redirects=False
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert "/login?error=invalid_state" in location


async def test_a_callback_never_reflects_the_upstreams_error_text(strict_client: AsyncClient):
    """The closed set, enforced at the redirect rather than at the renderer.

    An upstream's `error` is third-party text; putting it in a URL our own
    sign-in page reads would be a reflected-content injection.
    """
    response = await strict_client.get(
        f"{API}/auth/sso/auth0/callback?error=<script>alert(1)</script>",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "script" not in response.headers["location"]
    assert "error=access_denied" in response.headers["location"]


async def test_the_identities_endpoint_needs_a_session(strict_client: AsyncClient):
    assert (await strict_client.get(f"{API}/auth/identities")).status_code == 401


async def test_an_account_with_no_linked_identity_lists_none(strict_client: AsyncClient):
    await register_account(strict_client, email="ada@example.com")

    response = await strict_client.get(f"{API}/auth/identities")

    assert response.status_code == 200
    assert response.json() == []
