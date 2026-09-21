"""The OAuth 2.0 / OIDC exchange, shared by every provider.

Auth0 and Supabase differ in their endpoint paths, in the parameter that names
the upstream connection, and in which token carries the identity claims. They
do not differ in the protocol. So the protocol lives here once and each vendor
contributes a small description of itself, which is the difference between an
abstraction and two copies of the same code with the names changed.

Everything in this module that looks like ceremony is a specific attack being
closed:

* **PKCE (S256, always).** The authorization code comes back through the
  browser, which means through the user's address bar, their history, and any
  extension or app that can see a redirect. A code alone is enough to complete
  a sign-in; a code plus proof of the verifier that generated the challenge is
  not. Used even where a client secret is also held, because the secret does
  not protect a code that was stolen in transit. `plain` is never offered.
* **`state`.** Ties the callback to a flow this system started for this
  browser. Without it, an attacker completes a sign-in with *their* account in
  the victim's browser - login CSRF - and the victim then works inside the
  attacker's account, handing over whatever they do there.
* **`nonce`.** Ties the returned ID token to this particular request, so a
  token captured from another flow cannot be replayed into this one. Only
  meaningful where the provider echoes it, which is why it is described per
  vendor rather than assumed.
* **An asymmetric-only algorithm allowlist.** The single most common JWT
  vulnerability is algorithm confusion: a verifier that accepts whatever the
  token's own header asks for will accept `alg: none`, or accept `HS256` and
  use the provider's *public* key as an HMAC secret - which is public, so
  anybody can mint a valid token for anybody. The allowlist is fixed at
  verification and never read from the token.
* **`iss` and `aud` checked exactly.** A signature only proves a key signed it.
  Without an issuer check, a token from a different tenant of the same
  multi-tenant provider verifies fine; without an audience check, a token
  minted for a different application does.

**None of this has been run against a real provider.** There are no Auth0 or
Supabase credentials on this machine, so the flow is exercised end to end
against a scripted upstream over `httpx2.MockTransport` - the same way the
model providers are tested. The shapes follow each vendor's published
documentation. Treat the live round trip as unverified until someone runs it
with credentials.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import secrets
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

from joserfc import jwt
from joserfc.jwt import JWTClaimsRegistry

from app.auth.providers.base import (
    AuthorizationRequest,
    Connection,
    IdentityProvider,
    ProviderClaims,
    ProviderError,
    ProviderName,
    ProviderRefused,
    ProviderTokens,
)
from app.auth.providers.jwks import JwksCache
from app.core.logging import get_logger

logger = get_logger(__name__)

#: The only signature algorithms accepted, on any provider. Asymmetric only:
#: see the module docstring on algorithm confusion. `none` and every `HS*` are
#: absent on purpose and must stay absent.
ALLOWED_ALGORITHMS: tuple[str, ...] = ("RS256", "RS384", "RS512", "ES256", "ES384")

#: Seconds of clock skew tolerated on `exp`, `iat` and `nbf`. Small: this is
#: for two correctly-configured servers disagreeing slightly, not for keeping
#: expired tokens alive.
CLOCK_SKEW_SECONDS = 30

#: Bytes of entropy behind `state`, the nonce and the PKCE verifier. All three
#: are unguessability controls, and all three come from the OS CSPRNG.
ENTROPY_BYTES = 32

#: A token endpoint returns a small JSON document. A larger one is not a token
#: response and reading it would let an upstream choose this process's memory.
MAX_TOKEN_RESPONSE_BYTES = 128 * 1024

DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class OidcDescription:
    """Everything that differs between one vendor and another."""

    name: ProviderName
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str

    #: The query parameter that names the upstream, and what to call each one.
    #: Auth0 says `connection=google-oauth2`; Supabase says `provider=google`.
    connection_parameter: str
    connection_values: dict[Connection, str]

    #: Which field of the token response carries the identity claims. Standard
    #: OIDC says `id_token`; Supabase's social flow puts them in its own
    #: `access_token`.
    identity_token_field: str = "id_token"  # noqa: S105 - a field name, not a secret

    #: Whether the provider echoes `nonce` into the identity token. When it
    #: does, the nonce is *required* to match - a provider that echoes it
    #: sometimes and not others would make this check meaningless, so it is
    #: declared rather than inferred from the token.
    echoes_nonce: bool = True

    #: What `aud` must equal, when it is not the client id. Standard OIDC puts
    #: the client id there; Supabase puts the literal `authenticated` in every
    #: token it signs. Declared per vendor because the audience check is what
    #: stops a token minted for a different application being accepted here,
    #: and guessing at it would mean either a broken provider or a skipped
    #: check - and a skipped check is the one that fails silently.
    audience: str | None = None

    scopes: tuple[str, ...] = ("openid", "profile", "email")

    #: Extra parameters every authorization request carries.
    extra_authorize_params: dict[str, str] = field(default_factory=dict)

    # --- Token-request shape -------------------------------------------------
    # Supabase's PKCE exchange is not RFC 6749's. It names the code `auth_code`,
    # carries `grant_type` in the query string rather than the body, wants no
    # `redirect_uri`, and requires the project's publishable key as a header.
    # Described here rather than branched on in `exchange`, so that adding a
    # third vendor is a description and not another `if provider == ...`.

    #: What the token request calls the authorization code.
    code_parameter: str = "code"
    #: Whether `grant_type=authorization_code` goes in the body. Supabase puts
    #: its own grant type in the endpoint URL instead.
    sends_grant_type: bool = True
    #: Whether the token request repeats `redirect_uri`. Required by RFC 6749
    #: when the authorization request carried one; Supabase rejects it.
    sends_redirect_uri: bool = True
    #: Headers every token request carries, beyond `Accept`.
    extra_token_headers: dict[str, str] = field(default_factory=dict)


class OidcProvider(IdentityProvider):
    """One OIDC provider, described by an :class:`OidcDescription`."""

    def __init__(
        self,
        description: OidcDescription,
        *,
        client_id: str,
        client_secret: str | None,
        connections: tuple[Connection, ...],
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        jwks: JwksCache | None = None,
        transport: Any | None = None,
    ) -> None:
        unknown = set(connections) - set(description.connection_values)
        if unknown:
            raise ValueError(
                f"{description.name} cannot broker {sorted(unknown)}; "
                f"it knows {sorted(description.connection_values)}"
            )

        self._description = description
        self._client_id = client_id
        self._client_secret = client_secret
        self._connections = connections
        self._timeout = timeout_seconds
        self._transport = transport
        self._jwks = (
            jwks if jwks is not None else JwksCache(description.jwks_uri, transport=transport)
        )

    @property
    def name(self) -> ProviderName:
        return self._description.name

    @property
    def connections(self) -> tuple[Connection, ...]:
        return self._connections

    def authorize(self, *, connection: Connection, redirect_uri: str) -> AuthorizationRequest:
        if connection not in self._connections:
            # Not a user-facing case: the route resolves the connection against
            # this same list before calling. Raising rather than falling back to
            # a default keeps a configuration mistake loud.
            raise ValueError(f"{self.name} is not configured to broker {connection!r}")

        state = secrets.token_urlsafe(ENTROPY_BYTES)
        nonce = secrets.token_urlsafe(ENTROPY_BYTES)
        verifier = secrets.token_urlsafe(ENTROPY_BYTES)

        params: dict[str, str] = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self._description.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": _s256_challenge(verifier),
            "code_challenge_method": "S256",
            self._description.connection_parameter: (
                self._description.connection_values[connection]
            ),
            **self._description.extra_authorize_params,
        }

        separator = "&" if "?" in self._description.authorization_endpoint else "?"
        url = f"{self._description.authorization_endpoint}{separator}{urlencode(params)}"

        return AuthorizationRequest(url=url, state=state, nonce=nonce, code_verifier=verifier)

    async def exchange(
        self,
        *,
        code: str,
        code_verifier: str,
        nonce: str,
        redirect_uri: str,
    ) -> ProviderTokens:
        description = self._description
        form: dict[str, str] = {
            description.code_parameter: code,
            "code_verifier": code_verifier,
            "client_id": self._client_id,
        }
        if description.sends_grant_type:
            form["grant_type"] = "authorization_code"
        if description.sends_redirect_uri:
            form["redirect_uri"] = redirect_uri
        if self._client_secret:
            form["client_secret"] = self._client_secret

        payload = await self._post_token_request(form)

        raw_identity = payload.get(self._description.identity_token_field)
        if not isinstance(raw_identity, str) or not raw_identity:
            raise ProviderError(
                "That sign-in provider did not return an identity token.",
                code="identity_token_missing",
            )

        claims = await self._verify_identity_token(raw_identity, nonce=nonce)

        access_token = payload.get("access_token")
        refresh_token = payload.get("refresh_token")
        expires_at: dt.datetime | None = None
        expires_in = payload.get("expires_in")
        if isinstance(expires_in, int | float) and expires_in > 0:
            expires_at = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=int(expires_in))

        return ProviderTokens(
            claims=claims,
            access_token=access_token if isinstance(access_token, str) else None,
            refresh_token=refresh_token if isinstance(refresh_token, str) else None,
            expires_at=expires_at,
        )

    async def _post_token_request(self, form: dict[str, str]) -> dict[str, Any]:
        import httpx2

        try:
            async with httpx2.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.post(
                    self._description.token_endpoint,
                    data=form,
                    headers={
                        "Accept": "application/json",
                        **self._description.extra_token_headers,
                    },
                )
        except Exception as exc:
            raise ProviderError(
                "That sign-in provider is unavailable.",
                code="token_endpoint_unreachable",
            ) from exc

        if response.status_code >= 400:
            # An OAuth error response is JSON with an `error` code. `access_denied`
            # and `invalid_grant` are the user's doing - a cancelled consent, a
            # code already used or expired - and are not this system failing.
            error = _oauth_error_code(response)
            if error in {"access_denied", "invalid_grant", "consent_required"}:
                raise ProviderRefused(
                    "That sign-in was not completed.",
                    code="access_denied",
                )
            # The code is logged, never returned: an upstream error string is
            # attacker-influencable and can carry a configuration detail.
            logger.warning(
                "token exchange refused",
                extra={"provider": self.name, "status": response.status_code, "oauth_error": error},
            )
            raise ProviderError(
                "That sign-in could not be completed.",
                code="token_exchange_failed",
            )

        if len(response.content) > MAX_TOKEN_RESPONSE_BYTES:
            raise ProviderError(
                "That sign-in provider returned an unusable response.",
                code="token_response_too_large",
            )

        try:
            payload: Any = response.json()
        except Exception as exc:
            raise ProviderError(
                "That sign-in provider returned an unusable response.",
                code="token_response_malformed",
            ) from exc

        if not isinstance(payload, dict):
            raise ProviderError(
                "That sign-in provider returned an unusable response.",
                code="token_response_malformed",
            )
        return payload

    async def _verify_identity_token(self, token: str, *, nonce: str) -> ProviderClaims:
        """Check the signature and every claim that matters, then normalise."""
        kid = _unverified_kid(token)
        key_set = await self._jwks.key_set(kid=kid)

        try:
            # `algorithms` is passed explicitly and is the fixed allowlist. This
            # single argument is what makes `alg: none` and HS256 key confusion
            # impossible rather than merely unlikely.
            decoded = jwt.decode(token, key_set, algorithms=list(ALLOWED_ALGORITHMS))
        except Exception as exc:
            raise ProviderRefused(
                "That sign-in could not be verified.",
                code="identity_token_invalid",
            ) from exc

        registry = JWTClaimsRegistry(
            leeway=CLOCK_SKEW_SECONDS,
            iss={"essential": True, "value": self._description.issuer},
            aud={
                "essential": True,
                "value": self._description.audience or self._client_id,
            },
            exp={"essential": True},
            sub={"essential": True},
        )
        try:
            registry.validate(decoded.claims)
        except Exception as exc:
            raise ProviderRefused(
                "That sign-in could not be verified.",
                code="identity_claims_invalid",
            ) from exc

        claims = decoded.claims

        if self._description.echoes_nonce:
            returned = claims.get("nonce")
            # `secrets.compare_digest` rather than `==`. The nonce is not a
            # secret an attacker is guessing byte by byte, so this is belt and
            # braces - but a constant-time comparison costs nothing and means
            # nobody has to reason about whether it mattered here.
            if not isinstance(returned, str) or not secrets.compare_digest(returned, nonce):
                raise ProviderRefused(
                    "That sign-in could not be verified.",
                    code="nonce_mismatch",
                )

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise ProviderRefused(
                "That sign-in could not be verified.",
                code="subject_missing",
            )

        email = claims.get("email")
        verified = claims.get("email_verified")
        name = claims.get("name")

        return ProviderClaims(
            subject=subject,
            connection=_connection_from_subject(subject),
            email=email.strip().casefold() if isinstance(email, str) and email else None,
            # Only the boolean `True` counts. Providers have been known to send
            # the string "true", and a truthiness test would also accept "false".
            email_verified=verified is True,
            name=name if isinstance(name, str) and name else None,
        )


def _s256_challenge(verifier: str) -> str:
    """The PKCE challenge: base64url(SHA-256(verifier)), unpadded (RFC 7636)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _unverified_kid(token: str) -> str | None:
    """The `kid` from the token header, read before anything is verified.

    Safe because of what it is used for: choosing which public key to *try*. A
    forged `kid` selects the wrong key and the signature check then fails,
    which is the same outcome as naming no key at all.
    """
    try:
        header_segment = token.split(".", 1)[0]
        padded = header_segment + "=" * (-len(header_segment) % 4)
        import json

        header = json.loads(base64.urlsafe_b64decode(padded))
    except Exception:
        return None
    kid = header.get("kid") if isinstance(header, dict) else None
    return kid if isinstance(kid, str) else None


def _connection_from_subject(subject: str) -> Connection | None:
    """Which upstream a provider's subject came from, when it is legible.

    Auth0 prefixes subjects with the connection (`google-oauth2|1234`), which
    is the only place that information appears in the token. It is used for
    labelling a linked identity in the UI and for nothing else - in particular
    it is never part of the join key, which is the whole subject string.
    """
    prefix, separator, _ = subject.partition("|")
    if not separator:
        return None
    if prefix.startswith("google"):
        return "google"
    if prefix.startswith("github"):
        return "github"
    return None


def _oauth_error_code(response: Any) -> str | None:
    try:
        body = response.json()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    return error if isinstance(error, str) else None
