"""Supabase holds the passwords (ADR 0025).

Driven through the real Supabase client, the real provider, the real service
and the real endpoints, with only Supabase itself scripted - in memory, over
`httpx2.MockTransport`, signing real ES256 tokens with the claim shape a live
project issues.

**The script was written from a live project, not from the documentation.**
That is the point of it. The previous Supabase path was tested against a
script written from the same reading of the docs as the code, so the two agreed
with each other and both disagreed with Supabase in three places. Each of those
is pinned here by a test that the old code fails:

* the token endpoint parses JSON, and answers a form body with `bad_json`;
* `/authorize` takes `redirect_to` and does not echo `state`;
* tokens carry no `email_verified`; `user.email_confirmed_at` is the fact.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from httpx import ASGITransport, AsyncClient
from joserfc import jwt
from joserfc.jwk import ECKey, KeySet
from pydantic import SecretStr
from sqlalchemy import select

from app.api.deps import get_providers, get_supabase_auth
from app.auth.providers.base import ProviderError, ProviderRefused
from app.auth.providers.supabase import SupabaseProvider
from app.auth.supabase import ConfirmationPending, SupabaseAuth
from app.core.config import Settings
from app.core.enums import AuditAction
from app.core.errors import (
    AppError,
    EmailDeliveryUnavailable,
    EmailNotConfirmed,
    InvalidCredentials,
    RateLimited,
    ValidationFailed,
)
from app.db.models.audit import AuditLogRow
from app.db.models.user import IdentityRow, UserRow
from app.db.session import Database
from app.main import create_app
from tests.conftest import API, BASE_URL, TEST_PASSWORD

PROJECT_URL = "https://project-ref.supabase.co"
ISSUER = f"{PROJECT_URL}/auth/v1"
PUBLISHABLE_KEY = "sb_publishable_test"
SECRET_KEY = "sb_secret_test"  # noqa: S105 - a scripted project, not a credential
WRONG_PASSWORD = "not-the-password"  # noqa: S105 - a test fixture, not a credential


# --------------------------------------------------------------------------
# Supabase Auth, scripted
# --------------------------------------------------------------------------


@dataclass
class ScriptedUser:
    id: str
    email: str
    password: str
    confirmed: bool
    name: str | None = None
    provider: str = "email"


@dataclass
class ScriptedSupabase:
    """One Supabase project's Auth API, in memory, shaped like the live one."""

    autoconfirm: bool = False
    key: ECKey = field(default_factory=lambda: ECKey.generate_key("P-256"))
    users: dict[str, ScriptedUser] = field(default_factory=dict)
    #: auth_code -> (code_challenge, email)
    flows: dict[str, tuple[str, str]] = field(default_factory=dict)
    requests: list[httpx2.Request] = field(default_factory=list)
    #: Answers to give, in order, before behaving normally.
    script: list[httpx2.Response | Exception] = field(default_factory=list)
    #: Which social providers are switched on in the dashboard.
    enabled: set[str] = field(default_factory=lambda: {"google", "github"})

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle)

    def client(self, **kwargs: object) -> SupabaseAuth:
        options: dict[str, object] = {"publishable_key": PUBLISHABLE_KEY, **kwargs}
        return SupabaseAuth(url=PROJECT_URL, transport=self.transport, **options)  # type: ignore[arg-type]

    def add(
        self, email: str, *, password: str = TEST_PASSWORD, confirmed: bool = True
    ) -> ScriptedUser:
        user = ScriptedUser(
            id=str(uuid.uuid4()), email=email, password=password, confirmed=confirmed
        )
        self.users[email] = user
        return user

    def begin_oauth(self, *, code_challenge: str, email: str, connection: str) -> str:
        """What happens at Google and back: a user, and a code bound to the challenge."""
        if email not in self.users:
            user = self.add(email, password="", confirmed=True)
            user.provider = connection
            user.name = "Grace Hopper"
        code = str(uuid.uuid4())
        self.flows[code] = (code_challenge, email)
        return code

    def token(
        self,
        user: ScriptedUser,
        *,
        issuer: str = ISSUER,
        audience: str = "authenticated",
        key: ECKey | None = None,
    ) -> str:
        now = int(dt.datetime.now(dt.UTC).timestamp())
        signer = key or self.key
        claims: dict[str, object] = {
            "iss": issuer,
            "aud": audience,
            "sub": user.id,
            "exp": now + 3600,
            "iat": now,
            "email": user.email,
            "phone": "",
            "role": "authenticated",
            "aal": "aal1",
            "session_id": str(uuid.uuid4()),
            "is_anonymous": False,
            "app_metadata": {"provider": user.provider, "providers": [user.provider]},
            "user_metadata": {"name": user.name} if user.name else {},
            # Deliberately no `email_verified`: a live project does not send one.
        }
        header = {"alg": "ES256", "kid": signer.thumbprint(), "typ": "JWT"}
        return jwt.encode(header, claims, signer, algorithms=["ES256"])

    def session(self, user: ScriptedUser, **token_options: object) -> dict[str, object]:
        return {
            "access_token": self.token(user, **token_options),  # type: ignore[arg-type]
            "token_type": "bearer",
            "expires_in": 3600,
            "refresh_token": "supabase-refresh",
            "user": self._user_body(user),
        }

    def _user_body(self, user: ScriptedUser) -> dict[str, object]:
        return {
            "id": user.id,
            "aud": "authenticated",
            "email": user.email,
            "email_confirmed_at": "2026-10-01T00:00:00Z" if user.confirmed else None,
            "app_metadata": {"provider": user.provider},
            "user_metadata": {"name": user.name} if user.name else {},
        }

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.script:
            answer = self.script.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        path = request.url.path
        if path.endswith("/.well-known/jwks.json"):
            return httpx2.Response(200, json=KeySet([self.key]).as_dict(private=False))
        if path.endswith("/settings"):
            external = {name: name in self.enabled for name in ("google", "github", "apple")}
            return httpx2.Response(200, json={"external": {**external, "email": True}})

        try:
            body = json.loads(request.content)
        except ValueError:
            # What a live project says to a form-encoded body.
            return _error(400, "bad_json", "Could not parse request body as JSON")

        if path.endswith("/signup"):
            return self._signup(body)
        if path.endswith("/token"):
            grant = request.url.params.get("grant_type")
            if grant == "password":
                return self._password(body)
            if grant == "pkce":
                return self._pkce(body)
        if path.endswith("/resend"):
            return httpx2.Response(200, json={})
        return httpx2.Response(404)

    def _signup(self, body: dict[str, object]) -> httpx2.Response:
        email = str(body["email"])
        existing = self.users.get(email)
        if existing is not None and existing.confirmed:
            if self.autoconfirm:
                return _error(422, "user_already_exists", "User already registered")
            # Obfuscated: the same shape as a new sign-up, and nothing is sent.
            obfuscated = {"id": str(uuid.uuid4()), "email": email, "identities": []}
            return httpx2.Response(200, json=obfuscated)
        data = body.get("data")
        user = self.add(email, password=str(body["password"]), confirmed=self.autoconfirm)
        if isinstance(data, dict) and isinstance(data.get("name"), str):
            user.name = data["name"]
        if self.autoconfirm:
            return httpx2.Response(200, json=self.session(user))
        return httpx2.Response(200, json=self._user_body(user))

    def _password(self, body: dict[str, object]) -> httpx2.Response:
        user = self.users.get(str(body.get("email")))
        if user is None or not user.password or user.password != body.get("password"):
            return _error(400, "invalid_credentials", "Invalid login credentials")
        if not user.confirmed:
            return _error(400, "email_not_confirmed", "Email not confirmed")
        return httpx2.Response(200, json=self.session(user))

    def _pkce(self, body: dict[str, object]) -> httpx2.Response:
        flow = self.flows.pop(str(body.get("auth_code")), None)
        if flow is None:
            return _error(404, "flow_state_not_found", "invalid flow state")
        challenge, email = flow
        if _s256(str(body.get("code_verifier"))) != challenge:
            return _error(400, "bad_code_verifier", "code challenge does not match")
        return httpx2.Response(200, json=self.session(self.users[email]))


def _error(status: int, code: str, message: str) -> httpx2.Response:
    return httpx2.Response(status, json={"code": status, "error_code": code, "msg": message})


def _s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _calls(supabase: ScriptedSupabase, path: str) -> list[httpx2.Request]:
    """The requests made to one endpoint - not the key fetches between them."""
    return [request for request in supabase.requests if request.url.path.endswith(path)]


def _sent(request: httpx2.Request) -> dict[str, object]:
    body: dict[str, object] = json.loads(request.content)
    return body


# --------------------------------------------------------------------------
# The client: what it sends
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_request_is_json_because_supabase_refuses_a_form() -> None:
    """The first of the three live findings. A form body gets `400 bad_json`."""
    supabase = ScriptedSupabase()
    supabase.add("ada@example.com")

    await supabase.client().sign_in_with_password(
        email="ada@example.com", password=TEST_PASSWORD, client_ip=None
    )

    sent = _calls(supabase, "/token")[-1]
    assert sent.headers["content-type"] == "application/json"
    assert sent.url.params["grant_type"] == "password"
    assert _sent(sent) == {"email": "ada@example.com", "password": TEST_PASSWORD}


@pytest.mark.asyncio
async def test_the_code_exchange_is_supabases_pkce_grant_not_rfc_6749s() -> None:
    supabase = ScriptedSupabase()
    code = supabase.begin_oauth(
        code_challenge=_s256("v" * 43), email="g@example.com", connection="google"
    )

    await supabase.client().exchange_code(auth_code=code, code_verifier="v" * 43)

    sent = _calls(supabase, "/token")[-1]
    assert sent.url.params["grant_type"] == "pkce"
    assert _sent(sent) == {"auth_code": code, "code_verifier": "v" * 43}
    assert sent.headers["apikey"] == PUBLISHABLE_KEY


@pytest.mark.asyncio
async def test_a_sign_up_carries_the_name_and_where_the_link_should_land() -> None:
    supabase = ScriptedSupabase()

    await supabase.client().sign_up(
        email="ada@example.com",
        password=TEST_PASSWORD,
        name="Ada",
        redirect_to="http://localhost:3000/login?confirmed=1",
        client_ip=None,
    )

    sent = _calls(supabase, "/signup")[-1]
    assert sent.url.params["redirect_to"] == "http://localhost:3000/login?confirmed=1"
    assert _sent(sent)["data"] == {"name": "Ada"}


@pytest.mark.asyncio
async def test_the_end_users_address_is_forwarded_only_with_a_secret_key() -> None:
    """Supabase only honours `Sb-Forwarded-For` alongside a secret key, and
    without it every user shares this server's rate-limit bucket."""
    supabase = ScriptedSupabase()
    supabase.add("ada@example.com")

    await supabase.client().sign_in_with_password(
        email="ada@example.com", password=TEST_PASSWORD, client_ip="203.0.113.7"
    )
    assert "sb-forwarded-for" not in _calls(supabase, "/token")[-1].headers
    assert _calls(supabase, "/token")[-1].headers["apikey"] == PUBLISHABLE_KEY

    await supabase.client(secret_key=SECRET_KEY).sign_in_with_password(
        email="ada@example.com", password=TEST_PASSWORD, client_ip="203.0.113.7"
    )
    assert _calls(supabase, "/token")[-1].headers["sb-forwarded-for"] == "203.0.113.7"
    assert _calls(supabase, "/token")[-1].headers["apikey"] == SECRET_KEY


def test_a_plain_http_project_url_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        SupabaseAuth(url="http://project-ref.supabase.co", publishable_key=PUBLISHABLE_KEY)


# --------------------------------------------------------------------------
# The client: what it believes
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_password_sign_in_returns_the_verified_supabase_user() -> None:
    supabase = ScriptedSupabase()
    user = supabase.add("ada@example.com")
    user.name = "Ada"

    identity = await supabase.client().sign_in_with_password(
        email="ada@example.com", password=TEST_PASSWORD, client_ip=None
    )

    assert identity.subject == user.id
    assert identity.email == "ada@example.com"
    assert identity.email_confirmed is True
    assert identity.name == "Ada"
    assert identity.claims().usable_email == "ada@example.com"


@pytest.mark.asyncio
async def test_confirmation_comes_from_email_confirmed_at_not_a_token_claim() -> None:
    """The third live finding: Supabase's tokens carry no `email_verified`, so
    reading the claim - as the generic OIDC code does - finds every address
    unverified and refuses every new account."""
    supabase = ScriptedSupabase()
    user = supabase.add("ada@example.com")
    token = supabase.token(user)
    claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    assert "email_verified" not in claims

    identity = await supabase.client().sign_in_with_password(
        email="ada@example.com", password=TEST_PASSWORD, client_ip=None
    )
    assert identity.email_confirmed is True


@pytest.mark.asyncio
async def test_a_body_naming_another_user_is_not_trusted_about_confirmation() -> None:
    supabase = ScriptedSupabase()
    user = supabase.add("ada@example.com")
    session = supabase.session(user)
    session["user"] = {**session["user"], "id": str(uuid.uuid4())}  # type: ignore[dict-item]
    supabase.script.append(httpx2.Response(200, json=session))

    identity = await supabase.client().sign_in_with_password(
        email="ada@example.com", password=TEST_PASSWORD, client_ip=None
    )

    assert identity.subject == user.id
    assert identity.email_confirmed is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("forgery", "options"),
    [
        ("signed by another key", {"key": ECKey.generate_key("P-256")}),
        ("issued by another project", {"issuer": "https://other-ref.supabase.co/auth/v1"}),
        ("an anon-audience token", {"audience": "anon"}),
    ],
)
async def test_a_session_whose_token_does_not_verify_is_refused(
    forgery: str, options: dict[str, object]
) -> None:
    supabase = ScriptedSupabase()
    user = supabase.add("ada@example.com")
    supabase.script.append(httpx2.Response(200, json=supabase.session(user, **options)))

    with pytest.raises(ProviderRefused):
        await supabase.client().sign_in_with_password(
            email="ada@example.com", password=TEST_PASSWORD, client_ip=None
        )


@pytest.mark.asyncio
async def test_a_wrong_password_and_an_unknown_address_are_the_same_refusal() -> None:
    supabase = ScriptedSupabase()
    supabase.add("ada@example.com")
    client = supabase.client()

    with pytest.raises(InvalidCredentials) as wrong:
        await client.sign_in_with_password(
            email="ada@example.com", password=WRONG_PASSWORD, client_ip=None
        )
    with pytest.raises(InvalidCredentials) as unknown:
        await client.sign_in_with_password(
            email="who@example.com", password=WRONG_PASSWORD, client_ip=None
        )

    assert wrong.value.message == unknown.value.message
    assert wrong.value.code == unknown.value.code == "invalid_credentials"


@pytest.mark.asyncio
async def test_an_unconfirmed_address_is_told_so_only_with_the_right_password() -> None:
    supabase = ScriptedSupabase()
    supabase.add("ada@example.com", confirmed=False)
    client = supabase.client()

    with pytest.raises(InvalidCredentials):
        await client.sign_in_with_password(
            email="ada@example.com", password=WRONG_PASSWORD, client_ip=None
        )
    with pytest.raises(EmailNotConfirmed):
        await client.sign_in_with_password(
            email="ada@example.com", password=TEST_PASSWORD, client_ip=None
        )


@pytest.mark.asyncio
async def test_signing_up_an_existing_address_looks_exactly_like_a_new_one() -> None:
    supabase = ScriptedSupabase()
    supabase.add("ada@example.com")
    client = supabase.client()

    taken = await client.sign_up(
        email="ada@example.com", password=TEST_PASSWORD, name="", redirect_to="x", client_ip=None
    )
    fresh = await client.sign_up(
        email="new@example.com", password=TEST_PASSWORD, name="", redirect_to="x", client_ip=None
    )

    assert taken == ConfirmationPending(email="ada@example.com")
    assert fresh == ConfirmationPending(email="new@example.com")


# --------------------------------------------------------------------------
# The client: Supabase's refusals, in our words
# --------------------------------------------------------------------------


async def _refused_with(answer: httpx2.Response) -> BaseException:
    supabase = ScriptedSupabase()
    supabase.script.append(answer)
    with pytest.raises(AppError) as caught:
        await supabase.client().sign_up(
            email="ada@example.com",
            password=TEST_PASSWORD,
            name="",
            redirect_to="x",
            client_ip=None,
        )
    return caught.value


@pytest.mark.asyncio
async def test_a_weak_password_is_explained_in_our_words_beside_the_field() -> None:
    refusal = await _refused_with(
        httpx2.Response(
            422,
            json={
                "code": 422,
                "error_code": "weak_password",
                "msg": "UPSTREAM TEXT",
                "weak_password": {"reasons": ["pwned"]},
            },
        )
    )

    assert isinstance(refusal, ValidationFailed)
    assert refusal.details == {
        "password": ["That password has appeared in a data breach. Choose a different one."]
    }
    assert "UPSTREAM" not in str(refusal.details) + refusal.message


@pytest.mark.asyncio
async def test_a_project_without_custom_smtp_is_a_delivery_failure_not_a_bad_address() -> None:
    """Supabase's built-in mailer only emails the project's team."""
    refusal = await _refused_with(_error(400, "email_address_not_authorized", "not authorized"))

    assert isinstance(refusal, EmailDeliveryUnavailable)
    assert refusal.status_code == 503


@pytest.mark.asyncio
async def test_the_mailers_hourly_limit_says_when_to_come_back() -> None:
    refusal = await _refused_with(_error(429, "over_email_send_rate_limit", "rate limited"))

    assert isinstance(refusal, RateLimited)
    assert refusal.headers is not None and int(refusal.headers["Retry-After"]) > 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        httpx2.Response(500, json={"msg": "boom"}),
        _error(401, "no_authorization", "Invalid API key"),
        httpx2.Response(502, content=b"<html>bad gateway</html>"),
    ],
)
async def test_anything_else_is_an_outage_and_never_leaks_upstream_text(
    answer: httpx2.Response,
) -> None:
    refusal = await _refused_with(answer)

    assert isinstance(refusal, ProviderError)
    assert refusal.status_code == 502
    assert "Invalid API key" not in refusal.message and "boom" not in refusal.message


@pytest.mark.asyncio
async def test_a_refused_connection_is_retried_once() -> None:
    supabase = ScriptedSupabase()
    supabase.add("ada@example.com")
    supabase.script.append(httpx2.ConnectError("refused"))

    identity = await supabase.client().sign_in_with_password(
        email="ada@example.com", password=TEST_PASSWORD, client_ip=None
    )

    assert identity.email == "ada@example.com"


@pytest.mark.asyncio
async def test_a_request_lost_after_sending_is_not_retried() -> None:
    """A sign-up that timed out after Supabase received it may have created the
    account and sent the email. Sending it again is the worse outcome."""
    supabase = ScriptedSupabase()
    supabase.script.append(httpx2.ReadTimeout("lost"))

    with pytest.raises(ProviderError):
        await supabase.client().sign_up(
            email="ada@example.com",
            password=TEST_PASSWORD,
            name="",
            redirect_to="x",
            client_ip=None,
        )

    assert len(supabase.requests) == 1


# --------------------------------------------------------------------------
# Google and GitHub through Supabase
# --------------------------------------------------------------------------


def test_the_authorization_url_is_the_shape_supabases_own_client_sends() -> None:
    """The second live finding: `redirect_to`, not `redirect_uri`, and `state`
    carried inside it because Supabase does not echo one."""
    provider = SupabaseProvider(ScriptedSupabase().client(), connections=("google", "github"))
    callback = "http://localhost:8000/api/v1/auth/sso/supabase/callback"

    request = provider.authorize(connection="github", redirect_uri=callback)

    url = urlsplit(request.url)
    params = {key: values[0] for key, values in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{ISSUER}/authorize"
    assert params["provider"] == "github"
    assert params["code_challenge_method"] == "s256"
    assert params["code_challenge"] == _s256(request.code_verifier)
    assert "redirect_uri" not in params
    assert params["redirect_to"] == f"{callback}?state={request.state}"
    # Handed to a browser: no key of any kind belongs in it.
    assert "apikey" not in params and PUBLISHABLE_KEY not in request.url


@pytest.mark.asyncio
async def test_a_provider_sign_in_reports_its_connection_and_confirmed_address() -> None:
    supabase = ScriptedSupabase()
    provider = SupabaseProvider(supabase.client(), connections=("google",))
    request = provider.authorize(connection="google", redirect_uri="http://cb")
    code = supabase.begin_oauth(
        code_challenge=_s256(request.code_verifier), email="grace@example.com", connection="google"
    )

    tokens = await provider.exchange(
        code=code,
        code_verifier=request.code_verifier,
        nonce=request.nonce,
        redirect_uri="http://cb",
    )

    assert tokens.claims.connection == "google"
    assert tokens.claims.usable_email == "grace@example.com"
    assert tokens.claims.name == "Grace Hopper"
    # Supabase's own tokens are not kept.
    assert tokens.access_token is None and tokens.refresh_token is None


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def test_the_supabase_backend_refuses_to_start_without_a_project(settings: Settings) -> None:
    values = settings.model_dump()
    values.update(auth_backend="supabase", supabase_url=None, supabase_publishable_key=None)
    with pytest.raises(ValueError, match="AUTH_BACKEND=supabase"):
        Settings(**values)


# --------------------------------------------------------------------------
# The endpoints, end to end
# --------------------------------------------------------------------------


@dataclass
class Stage:
    http: AsyncClient
    supabase: ScriptedSupabase


@pytest.fixture
async def stage(strict_settings: Settings) -> AsyncIterator[Stage]:
    """The real app with `AUTH_BACKEND=supabase`, Supabase scripted."""
    settings = strict_settings.model_copy(
        update={
            "auth_backend": "supabase",
            "supabase_url": PROJECT_URL,
            "supabase_publishable_key": SecretStr(PUBLISHABLE_KEY),
        }
    )
    supabase = ScriptedSupabase()
    client = supabase.client()
    app = create_app(settings)
    app.dependency_overrides[get_supabase_auth] = lambda: client
    app.dependency_overrides[get_providers] = lambda: {
        "supabase": SupabaseProvider(client, connections=("google", "github"))
    }
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as http,
        app.router.lifespan_context(app),
    ):
        yield Stage(http=http, supabase=supabase)


async def _audit(database: Database, action: AuditAction) -> list[AuditLogRow]:
    async with database.session() as session:
        result = await session.execute(select(AuditLogRow).where(AuditLogRow.action == action))
        return list(result.scalars())


async def _users(database: Database) -> list[UserRow]:
    async with database.session() as session:
        return list((await session.execute(select(UserRow))).scalars())


@pytest.mark.asyncio
async def test_sign_up_waits_for_the_emailed_link_and_creates_nothing_here(
    stage: Stage, database: Database
) -> None:
    response = await stage.http.post(
        f"{API}/auth/register",
        json={"email": "Ada@Example.com", "password": TEST_PASSWORD, "name": "Ada"},
    )

    assert response.status_code == 202, response.text
    assert response.json() == {"confirmation_required": True, "email": "ada@example.com"}
    assert "set-cookie" not in response.headers
    assert await _users(database) == []
    assert len(await _audit(database, AuditAction.REGISTER_PENDING)) == 1
    assert (await stage.http.get(f"{API}/auth/me")).status_code == 401


@pytest.mark.asyncio
async def test_the_first_sign_in_after_confirming_creates_the_account_linked_by_id(
    stage: Stage, database: Database
) -> None:
    await stage.http.post(
        f"{API}/auth/register", json={"email": "ada@example.com", "password": TEST_PASSWORD}
    )

    early = await stage.http.post(
        f"{API}/auth/login", json={"email": "ada@example.com", "password": TEST_PASSWORD}
    )
    assert early.status_code == 403
    assert early.json()["error"]["code"] == "email_not_confirmed"

    stage.supabase.users["ada@example.com"].confirmed = True  # the person clicked the link

    signed_in = await stage.http.post(
        f"{API}/auth/login", json={"email": "ada@example.com", "password": TEST_PASSWORD}
    )
    assert signed_in.status_code == 200, signed_in.text
    me = await stage.http.get(f"{API}/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "ada@example.com"

    async with database.session() as session:
        identity = (await session.execute(select(IdentityRow))).scalar_one()
        user = (await session.execute(select(UserRow))).scalar_one()
    assert identity.provider == "supabase"
    assert identity.subject == stage.supabase.users["ada@example.com"].id
    # Supabase holds the password; nothing here does.
    assert user.password_hash is None


@pytest.mark.asyncio
async def test_signing_in_again_is_the_same_account(stage: Stage, database: Database) -> None:
    stage.supabase.add("ada@example.com")

    for _ in range(2):
        response = await stage.http.post(
            f"{API}/auth/login", json={"email": "ada@example.com", "password": TEST_PASSWORD}
        )
        assert response.status_code == 200, response.text

    assert len(await _users(database)) == 1


@pytest.mark.asyncio
async def test_this_deployments_password_policy_applies_before_supabase_is_asked(
    stage: Stage,
) -> None:
    response = await stage.http.post(
        f"{API}/auth/register", json={"email": "ada@example.com", "password": "short"}
    )

    assert response.status_code == 422
    assert "password" in response.json()["error"]["details"]
    assert stage.supabase.requests == []


@pytest.mark.asyncio
async def test_a_wrong_password_is_the_ordinary_refusal(stage: Stage) -> None:
    stage.supabase.add("ada@example.com")

    response = await stage.http.post(
        f"{API}/auth/login", json={"email": "ada@example.com", "password": "not-the-password"}
    )

    assert response.status_code == 401
    error = response.json()["error"]
    assert error["code"] == "invalid_credentials"
    assert error["message"] == "That email and password do not match an account."


@pytest.mark.asyncio
async def test_resending_the_link_answers_the_same_for_any_address(stage: Stage) -> None:
    for address in ("ada@example.com", "nobody@example.com"):
        response = await stage.http.post(f"{API}/auth/confirmation/resend", json={"email": address})
        assert response.status_code == 202
        assert response.json() == {"confirmation_required": True, "email": address}

    assert [request.url.path for request in stage.supabase.requests].count("/auth/v1/resend") == 2


@pytest.mark.asyncio
async def test_closed_registration_does_not_reach_supabase(strict_settings: Settings) -> None:
    settings = strict_settings.model_copy(
        update={
            "auth_backend": "supabase",
            "supabase_url": PROJECT_URL,
            "supabase_publishable_key": SecretStr(PUBLISHABLE_KEY),
            "registration_enabled": False,
        }
    )
    supabase = ScriptedSupabase()
    app = create_app(settings)
    app.dependency_overrides[get_supabase_auth] = lambda: supabase.client()
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url=BASE_URL) as http,
        app.router.lifespan_context(app),
    ):
        response = await http.post(
            f"{API}/auth/register", json={"email": "ada@example.com", "password": TEST_PASSWORD}
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "registration_closed"
    assert supabase.requests == []


@pytest.mark.asyncio
async def test_google_through_supabase_completes_and_lands_where_it_was_going(
    stage: Stage, database: Database
) -> None:
    """The whole browser round trip, with Google itself the only thing skipped."""
    start = await stage.http.get(
        f"{API}/auth/sso/supabase/google/start", params={"next": "/dashboard"}
    )
    assert start.status_code == 307
    authorize = parse_qs(urlsplit(start.headers["location"]).query)
    redirect_to = urlsplit(authorize["redirect_to"][0])

    # Google, then Supabase, then back to the callback Supabase was given.
    code = stage.supabase.begin_oauth(
        code_challenge=authorize["code_challenge"][0],
        email="grace@example.com",
        connection="google",
    )
    callback = await stage.http.get(f"{redirect_to.path}?{redirect_to.query}&code={code}")

    assert callback.status_code == 303, callback.text
    assert callback.headers["location"].endswith("/dashboard")
    me = await stage.http.get(f"{API}/auth/me")
    assert me.status_code == 200
    assert me.json()["email"] == "grace@example.com"

    identities = (await stage.http.get(f"{API}/auth/identities")).json()
    assert [(row["provider"], row["connection"]) for row in identities] == [("supabase", "google")]


@pytest.mark.asyncio
async def test_a_code_from_someone_elses_flow_is_refused(stage: Stage) -> None:
    """PKCE: a stolen code is useless without the verifier this browser holds."""
    start = await stage.http.get(f"{API}/auth/sso/supabase/github/start")
    authorize = parse_qs(urlsplit(start.headers["location"]).query)
    redirect_to = urlsplit(authorize["redirect_to"][0])

    stolen = stage.supabase.begin_oauth(
        code_challenge=_s256("an-attackers-own-verifier-" + "x" * 20),
        email="mallory@example.com",
        connection="github",
    )
    callback = await stage.http.get(f"{redirect_to.path}?{redirect_to.query}&code={stolen}")

    assert callback.status_code == 303
    assert "/login?error=" in callback.headers["location"]
    assert (await stage.http.get(f"{API}/auth/me")).status_code == 401


# --------------------------------------------------------------------------
# Buttons only for providers the project has switched on
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_providers_switched_on_in_the_project_get_a_button(stage: Stage) -> None:
    stage.supabase.enabled = {"github"}

    options = (await stage.http.get(f"{API}/auth/sso/providers")).json()["options"]

    assert [option["connection"] for option in options] == ["github"]


@pytest.mark.asyncio
async def test_a_provider_that_is_switched_off_cannot_be_started(stage: Stage) -> None:
    stage.supabase.enabled = set()

    response = await stage.http.get(f"{API}/auth/sso/supabase/google/start")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "sso_not_available"


@pytest.mark.asyncio
async def test_the_configuration_stands_when_the_project_cannot_be_asked() -> None:
    supabase = ScriptedSupabase()
    supabase.script.append(httpx2.ConnectError("down"))
    provider = SupabaseProvider(supabase.client(), connections=("google", "github"))

    assert await provider.available_connections() == ("google", "github")


@pytest.mark.asyncio
async def test_switching_a_provider_on_shows_up_within_the_cache_window() -> None:
    supabase = ScriptedSupabase(enabled=set())
    client = supabase.client()
    provider = SupabaseProvider(client, connections=("google", "github"))
    assert await provider.available_connections() == ()

    supabase.enabled = {"google"}
    client._enabled_at -= 61  # the dashboard was changed a minute ago

    assert await provider.available_connections() == ("google",)
