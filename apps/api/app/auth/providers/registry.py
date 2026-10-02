"""Which providers this deployment has, built from settings.

Auth0's description for the shared OIDC code, the Supabase provider, and the
rule for turning configuration into providers. Auth0 is described rather than
implemented because it speaks standard OIDC; Supabase has its own class
(`supabase.py`) because, checked against a live project, it does not (ADR 0025).
Everything after the provider - the linking, the sessions, the endpoints - is
shared.

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
from app.auth.providers.supabase import SupabaseProvider
from app.auth.supabase import build_supabase_auth
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
    auth = build_supabase_auth(
        url=settings.supabase_url,
        publishable_key=(
            settings.supabase_publishable_key.get_secret_value()
            if settings.supabase_publishable_key
            else None
        ),
        secret_key=(
            settings.supabase_secret_key.get_secret_value()
            if settings.supabase_secret_key
            else None
        ),
        timeout_seconds=settings.sso_request_timeout_seconds,
    )
    if auth is None:
        return None
    return SupabaseProvider(auth, connections=connections)
