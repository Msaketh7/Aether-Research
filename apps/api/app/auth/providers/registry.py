"""Which providers this deployment has, built from settings.

Two vendor descriptions and the rule for turning configuration into providers.
The descriptions are the only vendor-specific code in the system: everything
else - the exchange, the verification, the linking, the endpoints - is shared.

**A provider is built only when it is fully configured.** A half-configured
one is not offered at all rather than being offered and failing at the
callback, because the second is indistinguishable from an outage to the person
trying to sign in and produces a support ticket instead of a startup error.

**Unconfigured is not an error.** A deployment with no Auth0 credentials is a
deployment that does not offer Auth0, which is the normal case for local
development and for the test suite. Refusing to start would make SSO a
requirement rather than a feature.
"""

from __future__ import annotations

from typing import cast

from app.auth.providers.base import Connection, IdentityProvider, ProviderName
from app.auth.providers.oidc import OidcDescription, OidcProvider
from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def auth0_description(domain: str) -> OidcDescription:
    """Auth0's endpoints for a tenant domain.

    The issuer carries a trailing slash. That is not a typo and not cosmetic:
    Auth0 mints tokens with `iss` exactly `https://<domain>/`, and the issuer
    check is an exact string comparison, so dropping it fails every sign-in
    with a claims error that looks nothing like a missing slash.
    """
    base = f"https://{domain.strip().rstrip('/')}"
    return OidcDescription(
        name="auth0",
        issuer=f"{base}/",
        authorization_endpoint=f"{base}/authorize",
        token_endpoint=f"{base}/oauth/token",
        jwks_uri=f"{base}/.well-known/jwks.json",
        # Auth0 selects the upstream with `connection`, and names Google's
        # connection `google-oauth2` rather than `google`.
        connection_parameter="connection",
        connection_values={"google": "google-oauth2", "github": "github"},
        identity_token_field="id_token",  # noqa: S106 - a field name, not a secret
        echoes_nonce=True,
    )


def supabase_description(project_url: str, publishable_key: str) -> OidcDescription:
    """Supabase GoTrue's endpoints for a project.

    Three departures from standard OIDC, all of them Supabase's:

    * The identity claims are in its own `access_token`, not an `id_token`.
    * The PKCE exchange names the code `auth_code`, puts the grant type in the
      endpoint's query string, and rejects a repeated `redirect_uri`.
    * Every request carries the project's publishable key as `apikey`.

    **The project must be using asymmetric signing keys.** Supabase originally
    signed with a shared HS256 secret, and this system's algorithm allowlist
    contains no HMAC algorithm - deliberately, because accepting one is how
    algorithm-confusion attacks work. A project still on a legacy JWT secret
    will fail verification, and that is the correct outcome rather than a bug
    to work around: the fix is to migrate the project to ES256 keys, which is
    also what Supabase now recommends.
    """
    base = project_url.strip().rstrip("/")
    return OidcDescription(
        name="supabase",
        issuer=f"{base}/auth/v1",
        authorization_endpoint=f"{base}/auth/v1/authorize",
        token_endpoint=f"{base}/auth/v1/token?grant_type=pkce",
        jwks_uri=f"{base}/auth/v1/.well-known/jwks.json",
        connection_parameter="provider",
        connection_values={"google": "google", "github": "github"},
        identity_token_field="access_token",  # noqa: S106 - a field name, not a secret
        # GoTrue signs every token with `aud: "authenticated"`.
        audience="authenticated",
        # GoTrue's PKCE flow does not echo a nonce. PKCE and `state` still bind
        # the callback to this flow and this browser; what is lost is replay
        # protection on the token itself, which is why the nonce is kept
        # wherever a provider does echo it rather than being dropped for all.
        echoes_nonce=False,
        code_parameter="auth_code",
        sends_grant_type=False,
        sends_redirect_uri=False,
        extra_token_headers={"apikey": publishable_key},
    )


def build_providers(settings: Settings) -> dict[ProviderName, IdentityProvider]:
    """Every provider this deployment can actually complete a sign-in with."""
    providers: dict[ProviderName, IdentityProvider] = {}

    # `sso_connections_tuple` has already dropped anything not in the known
    # set, so this cast states what that filtering guarantees rather than
    # asserting something unchecked.
    connections = cast("tuple[Connection, ...]", settings.sso_connections_tuple)
    if not connections:
        logger.info("no SSO connections enabled; single sign-on is off")
        return providers

    auth0 = _build_auth0(settings, connections)
    if auth0 is not None:
        providers["auth0"] = auth0

    supabase = _build_supabase(settings, connections)
    if supabase is not None:
        providers["supabase"] = supabase

    if providers:
        logger.info(
            "single sign-on enabled",
            extra={
                "sso_providers": sorted(providers),
                "sso_connections": sorted(connections),
            },
        )
    return providers


def _build_auth0(
    settings: Settings, connections: tuple[Connection, ...]
) -> IdentityProvider | None:
    domain = settings.auth0_domain
    client_id = settings.auth0_client_id
    secret = settings.auth0_client_secret

    if not (domain and client_id and secret):
        return None

    return OidcProvider(
        auth0_description(domain),
        client_id=client_id,
        client_secret=secret.get_secret_value(),
        connections=connections,
        timeout_seconds=settings.sso_request_timeout_seconds,
    )


def _build_supabase(
    settings: Settings, connections: tuple[Connection, ...]
) -> IdentityProvider | None:
    url = settings.supabase_url
    key = settings.supabase_publishable_key

    if not (url and key):
        return None

    return OidcProvider(
        supabase_description(url, key.get_secret_value()),
        client_id=settings.supabase_client_id or url.strip().rstrip("/"),
        # A public client: the PKCE verifier is the proof, not a secret this
        # system holds. Supabase's publishable key is not a client secret and
        # must not be sent as one - it is a per-project identifier that ships
        # in browsers.
        client_secret=None,
        connections=connections,
        timeout_seconds=settings.sso_request_timeout_seconds,
    )
