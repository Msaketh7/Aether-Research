"""Supabase Auth, spoken server-side (ADR 0025).

The only module in the system that talks to Supabase Auth's REST API (GoTrue).
Passwords, email confirmation and the Google and GitHub sign-ins all live in the
Supabase project; this is the client that asks it to do those things and turns
its answers into a verified identity the rest of the system already knows how
to handle.

**The browser never talks to Supabase.** Every call here is made by the API,
and what the browser ends up holding is this system's own `HttpOnly` session
cookie (ADR 0022), never a Supabase token. Supabase's tokens are verified,
read once, and discarded.

**Every shape here was checked against a live project**, not only against the
documentation - which is how three defects in the previous Supabase path were
found before anyone tried to sign in with it:

* GoTrue's token endpoint parses **JSON**, and answers a form body with
  ``400 bad_json``. The shared OIDC code posts a form, as RFC 6749 says to.
* ``/authorize`` takes ``redirect_to``, not OIDC's ``redirect_uri``, and does
  not echo ``state`` back. So the state travels inside ``redirect_to``.
* Supabase's tokens carry **no** ``email_verified`` claim. Whether the address
  is confirmed comes from ``user.email_confirmed_at`` in the same response.

**Retries are deliberately narrow.** A request that never reached Supabase -
a refused connection, a connect timeout - is retried once. Nothing else is: a
sign-up that timed out after Supabase received it may have created the
account and sent the email, and sending it again is the worse outcome.

**No upstream message is ever shown to anybody.** GoTrue's ``msg`` is logged
and mapped to one of a closed set of errors; the text a person sees is ours.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlencode

from joserfc import jwt
from joserfc.jwt import JWTClaimsRegistry

from app.auth.providers.base import Connection, ProviderClaims, ProviderError, ProviderRefused
from app.auth.providers.jwks import JwksCache
from app.auth.providers.oidc import (
    ALLOWED_ALGORITHMS,
    CLOCK_SKEW_SECONDS,
    MAX_TOKEN_RESPONSE_BYTES,
    _s256_challenge,
    _unverified_kid,
)
from app.core.errors import (
    AppError,
    EmailDeliveryUnavailable,
    EmailNotConfirmed,
    InvalidCredentials,
    RateLimited,
    RegistrationClosed,
    ValidationFailed,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

#: What GoTrue puts in `aud` on every token it issues to a signed-in user.
#: Checked, because it is what separates a user's token from the project's
#: `anon` and `service_role` tokens - which are signed by the same project.
AUDIENCE = "authenticated"

DEFAULT_TIMEOUT_SECONDS = 10.0

#: How long a person is told to wait after Supabase's mailer says no more. The
#: built-in mailer's limit is per hour; a custom SMTP project's is configurable.
EMAIL_RETRY_AFTER_SECONDS = 900

#: How long the project's list of switched-on providers is trusted. Short, so
#: switching Google on in the dashboard shows its button within a minute; not
#: zero, so rendering the sign-in page is not a request to Supabase every time.
PROVIDER_SETTINGS_TTL_SECONDS = 60.0

#: The connections a project can switch on, as `/settings` names them.
_CONNECTIONS: tuple[Connection, ...] = ("google", "github")

#: Supabase's reasons for refusing a password, in our words. A closed table:
#: an unknown reason gets the generic sentence rather than GoTrue's text.
_WEAK_PASSWORD_REASONS = {
    "length": "That password is shorter than this deployment allows.",
    "characters": "That password needs a wider mix of characters.",
    "pwned": "That password has appeared in a data breach. Choose a different one.",
}

Flow = Literal["signup", "password", "pkce"]


@dataclass(frozen=True, slots=True)
class SupabaseIdentity:
    """A Supabase user, after its token's signature and claims were checked."""

    #: `auth.users.id`, taken from the verified token's `sub`. The join key.
    subject: str
    email: str | None
    #: From `user.email_confirmed_at`. Supabase's tokens do not carry
    #: `email_verified`, so this is the only place the fact exists.
    email_confirmed: bool
    name: str | None
    #: `google` or `github` when the account came in through one, else None.
    connection: Connection | None

    def claims(self) -> ProviderClaims:
        """The same shape every other provider's sign-in resolves through."""
        return ProviderClaims(
            subject=self.subject,
            connection=self.connection,
            email=self.email,
            email_verified=self.email_confirmed,
            name=self.name,
        )


@dataclass(frozen=True, slots=True)
class ConfirmationPending:
    """A sign-up Supabase accepted and is now waiting on an emailed link for.

    Also what an *already registered* address gets back: GoTrue answers that
    case with the same shape and sends nothing, so that sign-up cannot be used
    to discover who has an account. This type preserves that - it carries no
    field that could tell the two apart.
    """

    email: str


class SupabaseAuth:
    """A server-side client for one Supabase project's Auth API."""

    def __init__(
        self,
        *,
        url: str,
        publishable_key: str,
        secret_key: str | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: Any | None = None,
        jwks: JwksCache | None = None,
    ) -> None:
        base = url.strip().rstrip("/")
        if not base.startswith("https://"):
            # Passwords travel over this connection. Refused at construction so
            # a typo is a startup error rather than a downgrade.
            raise ValueError(f"SUPABASE_URL must be HTTPS, got {base!r}")

        self.issuer = f"{base}/auth/v1"
        #: The secret key when there is one: only it may ask Supabase to rate
        #: limit by the end user's address (`Sb-Forwarded-For`) rather than by
        #: this server's, which every user shares.
        self._api_key = secret_key or publishable_key
        self._forwards_client_ip = secret_key is not None
        self._timeout = timeout_seconds
        self._transport = transport
        self._jwks = jwks or JwksCache(f"{self.issuer}/.well-known/jwks.json", transport=transport)
        self._enabled: frozenset[Connection] | None = None
        self._enabled_at = 0.0

    # --- what the project offers -----------------------------------------

    async def enabled_connections(self) -> frozenset[Connection] | None:
        """Which of Google and GitHub the project has switched on.

        Read from the project's public `/settings` rather than trusted from this
        deployment's configuration, because the two drift: a provider named in
        `SSO_CONNECTIONS` but not yet switched on in the dashboard is a button
        that lands on Supabase's own error page. `None` when the project has
        never answered; a failed refresh keeps the last answer it gave.
        """
        now = time.monotonic()
        if self._enabled is not None and now - self._enabled_at < PROVIDER_SETTINGS_TTL_SECONDS:
            return self._enabled

        import httpx2

        try:
            async with httpx2.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.get(
                    f"{self.issuer}/settings",
                    headers={"apikey": self._api_key, "Accept": "application/json"},
                )
            response.raise_for_status()
            external = response.json().get("external")
        except Exception:
            logger.warning("could not read which sign-in providers the project has enabled")
            return self._enabled

        self._enabled = frozenset(
            connection
            for connection in _CONNECTIONS
            if isinstance(external, dict) and external.get(connection) is True
        )
        self._enabled_at = now
        return self._enabled

    # --- OAuth (Google, GitHub) ------------------------------------------

    def authorize_url(self, *, connection: Connection, redirect_to: str, code_verifier: str) -> str:
        """Where to send the browser to sign in through `connection`.

        The shape is exactly what Supabase's own JavaScript client sends -
        `s256` in lowercase included. No API key: this URL is handed to a
        browser, and the publishable key is not needed to start the flow.
        """
        params = {
            "provider": connection,
            "redirect_to": redirect_to,
            "code_challenge": _s256_challenge(code_verifier),
            "code_challenge_method": "s256",
        }
        return f"{self.issuer}/authorize?{urlencode(params)}"

    async def exchange_code(self, *, auth_code: str, code_verifier: str) -> SupabaseIdentity:
        """Trade the code `/authorize` sent back for a verified identity."""
        payload = await self._post(
            "/token",
            {"auth_code": auth_code, "code_verifier": code_verifier},
            query={"grant_type": "pkce"},
            flow="pkce",
        )
        return await self._identity(payload)

    # --- passwords ---------------------------------------------------------

    async def sign_up(
        self,
        *,
        email: str,
        password: str,
        name: str,
        redirect_to: str,
        client_ip: str | None,
    ) -> SupabaseIdentity | ConfirmationPending:
        """Create an account.

        Returns an identity when the project confirms addresses automatically,
        and :class:`ConfirmationPending` when it emails a link first - which is
        Supabase's default, and the one a deployment should keep.
        """
        body: dict[str, Any] = {"email": email, "password": password}
        if name:
            body["data"] = {"name": name}
        payload = await self._post(
            "/signup",
            body,
            query={"redirect_to": redirect_to},
            client_ip=client_ip,
            flow="signup",
        )
        if isinstance(payload.get("access_token"), str):
            return await self._identity(payload)
        return ConfirmationPending(email=email)

    async def sign_in_with_password(
        self, *, email: str, password: str, client_ip: str | None
    ) -> SupabaseIdentity:
        payload = await self._post(
            "/token",
            {"email": email, "password": password},
            query={"grant_type": "password"},
            client_ip=client_ip,
            flow="password",
        )
        return await self._identity(payload)

    async def resend_confirmation(
        self, *, email: str, redirect_to: str, client_ip: str | None
    ) -> None:
        """Send the sign-up link again.

        Supabase answers `{}` whether or not the address has a pending
        sign-up, and so does this - it returns nothing either way.
        """
        await self._post(
            "/resend",
            {"type": "signup", "email": email},
            query={"redirect_to": redirect_to},
            client_ip=client_ip,
            flow="signup",
        )

    # --- transport -------------------------------------------------------

    async def _post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        query: dict[str, str],
        flow: Flow,
        client_ip: str | None = None,
    ) -> dict[str, Any]:
        headers = {
            "apikey": self._api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._forwards_client_ip and client_ip:
            headers["Sb-Forwarded-For"] = client_ip

        url = f"{self.issuer}{path}?{urlencode(query)}" if query else f"{self.issuer}{path}"
        response = await self._send(url, json.dumps(body), headers)

        if len(response.content) > MAX_TOKEN_RESPONSE_BYTES:
            raise ProviderError("Sign-in is unavailable right now.", code="auth_response_too_large")

        try:
            payload: Any = response.json() if response.content else {}
        except Exception:
            payload = None

        if response.status_code >= 400:
            raise _refusal(response.status_code, payload, flow=flow)

        if not isinstance(payload, dict):
            raise ProviderError("Sign-in is unavailable right now.", code="auth_response_malformed")
        return payload

    async def _send(self, url: str, content: str, headers: dict[str, str]) -> Any:
        import httpx2

        try:
            return await self._post_once(url, content, headers)
        except (httpx2.ConnectError, httpx2.ConnectTimeout):
            # The request never left: safe to send again, once.
            await asyncio.sleep(0.25)
        except Exception as exc:
            # Sent, and then lost. Not retried - see the module docstring.
            raise _unavailable() from exc
        try:
            return await self._post_once(url, content, headers)
        except Exception as exc:
            raise _unavailable() from exc

    async def _post_once(self, url: str, content: str, headers: dict[str, str]) -> Any:
        import httpx2

        async with httpx2.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            return await client.post(url, content=content, headers=headers)

    async def _identity(self, payload: dict[str, Any]) -> SupabaseIdentity:
        """Verify a session's access token and read the user it belongs to."""
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ProviderError("Sign-in is unavailable right now.", code="identity_token_missing")

        claims = await self._verify(token)
        subject = claims["sub"]

        user = payload.get("user")
        user = user if isinstance(user, dict) else {}
        if user.get("id") not in (None, subject):
            # The body and the signed token disagree about who this is. The
            # token is the one that is signed, so the body is not trusted for
            # anything - including whether the address is confirmed.
            logger.warning("supabase session body names a different user than its token")
            user = {}

        email = claims.get("email") or user.get("email")
        app_metadata = claims.get("app_metadata")
        user_metadata = claims.get("user_metadata")
        upstream = app_metadata.get("provider") if isinstance(app_metadata, dict) else None
        name = None
        if isinstance(user_metadata, dict):
            for field in ("name", "full_name", "user_name"):
                value = user_metadata.get(field)
                if isinstance(value, str) and value.strip():
                    name = value.strip()
                    break

        return SupabaseIdentity(
            subject=subject,
            email=email.strip().casefold() if isinstance(email, str) and email else None,
            email_confirmed=isinstance(user.get("email_confirmed_at"), str),
            name=name,
            connection=upstream if upstream in ("google", "github") else None,
        )

    async def _verify(self, token: str) -> dict[str, Any]:
        """Signature, issuer, audience, expiry - the same checks as any provider."""
        key_set = await self._jwks.key_set(kid=_unverified_kid(token))
        try:
            # The allowlist is fixed and asymmetric-only. Supabase projects on
            # the legacy shared HS256 secret fail here, by design (ADR 0022).
            decoded = jwt.decode(token, key_set, algorithms=list(ALLOWED_ALGORITHMS))
            JWTClaimsRegistry(
                leeway=CLOCK_SKEW_SECONDS,
                iss={"essential": True, "value": self.issuer},
                aud={"essential": True, "value": AUDIENCE},
                exp={"essential": True},
                sub={"essential": True},
            ).validate(decoded.claims)
        except Exception as exc:
            raise ProviderRefused(
                "That sign-in could not be verified.", code="identity_token_invalid"
            ) from exc

        claims = decoded.claims
        if not isinstance(claims.get("sub"), str) or not claims["sub"]:
            raise ProviderRefused("That sign-in could not be verified.", code="subject_missing")
        if claims.get("is_anonymous") is True:
            # Anonymous sign-ins are off in the project, and an anonymous
            # Supabase user is not an account here even if someone turns them on.
            raise ProviderRefused("That sign-in could not be verified.", code="anonymous_user")
        return dict(claims)


def _unavailable() -> ProviderError:
    return ProviderError("Sign-in is unavailable right now.", code="provider_unavailable")


def _refusal(status: int, payload: Any, *, flow: Flow) -> AppError:
    """GoTrue's error, as one of ours.

    Keyed on `error_code`, which GoTrue documents as stable, and never on the
    human-readable `msg`.
    """
    body = payload if isinstance(payload, dict) else {}
    error_code = body.get("error_code") or body.get("error")
    error_code = error_code if isinstance(error_code, str) else None

    if error_code == "invalid_credentials":
        return InvalidCredentials()

    if error_code == "email_not_confirmed":
        return EmailNotConfirmed()

    if error_code == "weak_password":
        detail = body.get("weak_password")
        reasons = detail.get("reasons") if isinstance(detail, dict) else None
        problems = [
            _WEAK_PASSWORD_REASONS[reason]
            for reason in (reasons if isinstance(reasons, list) else [])
            if reason in _WEAK_PASSWORD_REASONS
        ] or ["That password cannot be used."]
        return ValidationFailed("That password cannot be used.", details={"password": problems})

    if error_code in {"user_already_exists", "email_exists"}:
        # Only reachable when the project confirms addresses automatically;
        # with confirmation on, Supabase answers this case like a new sign-up.
        return ValidationFailed(
            "That account could not be created.",
            code="registration_refused",
            details={"email": ["This address cannot be registered."]},
        )

    if error_code == "email_address_invalid":
        return ValidationFailed(
            "Check the highlighted fields.",
            details={"email": ["Enter an email address that can receive mail."]},
        )

    if error_code in {"signup_disabled", "email_provider_disabled"}:
        return RegistrationClosed()

    if error_code == "email_address_not_authorized":
        logger.warning(
            "supabase refused to email a non-team address; the project needs custom SMTP"
        )
        return EmailDeliveryUnavailable()

    if error_code == "over_email_send_rate_limit":
        return RateLimited(
            "Too many emails have been sent. Try again later.",
            code="email_rate_limited",
            headers={"Retry-After": str(EMAIL_RETRY_AFTER_SECONDS)},
        )

    if status == 429:
        return RateLimited(headers={"Retry-After": "60"})

    if flow == "password" and status == 400 and error_code is None:
        # A password grant refused without a code is still a refused
        # credential; treating it as an outage would send people to retry.
        return InvalidCredentials()

    # Anything else is this deployment misconfigured or Supabase failing: an
    # invalid API key, a disabled grant, a 5xx. Logged with the code, never
    # surfaced, because nothing the person does will change it.
    logger.warning(
        "supabase auth refused a request",
        extra={"status": status, "supabase_error": error_code, "auth_flow": flow},
    )
    return _unavailable()


def build_supabase_auth(
    *,
    url: str | None,
    publishable_key: str | None,
    secret_key: str | None,
    timeout_seconds: float,
) -> SupabaseAuth | None:
    """A client when the project is configured, otherwise None."""
    if not (url and publishable_key):
        return None
    return SupabaseAuth(
        url=url,
        publishable_key=publishable_key,
        secret_key=secret_key,
        timeout_seconds=timeout_seconds,
    )
