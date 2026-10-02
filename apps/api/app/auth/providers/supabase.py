"""Google and GitHub, brokered by Supabase (ADR 0025).

Supabase used to be described to the shared OIDC code (`oidc.py`) as one more
OIDC provider. It is not one, in three ways that each broke the flow - and none
of which the scripted tests could see, because the script was written from the
same reading of the documentation as the code. Checked against a live project:

* ``/authorize`` takes ``redirect_to``, not ``redirect_uri``. Sent the OIDC
  parameter, Supabase ignores it and sends the person to the project's Site URL.
* Supabase does not echo ``state``. The callback requires one, so every sign-in
  would have ended in ``invalid_state``. Here ``state`` rides inside
  ``redirect_to`` as a query parameter, which Supabase preserves and appends
  ``code`` to - so the callback receives both, unchanged.
* The token endpoint wants JSON, and answers a form body with ``400 bad_json``.

So this provider delegates to :class:`app.auth.supabase.SupabaseAuth`, which
holds Supabase's actual protocol, and only adapts it to the interface.

**What is still unverified**: the full Google and GitHub round trip, which needs
OAuth applications registered with Google and GitHub and switched on in the
Supabase dashboard. Everything up to that point has been run live.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlencode, urlsplit, urlunsplit

from app.auth.providers.base import (
    AuthorizationRequest,
    Connection,
    IdentityProvider,
    ProviderName,
    ProviderTokens,
)
from app.auth.providers.oidc import ENTROPY_BYTES
from app.auth.supabase import SupabaseAuth


class SupabaseProvider(IdentityProvider):
    """One Supabase project, brokering the connections it has switched on."""

    def __init__(self, auth: SupabaseAuth, *, connections: tuple[Connection, ...]) -> None:
        self._auth = auth
        self._connections = connections

    @property
    def name(self) -> ProviderName:
        return "supabase"

    @property
    def connections(self) -> tuple[Connection, ...]:
        return self._connections

    async def available_connections(self) -> tuple[Connection, ...]:
        """The configured connections the project has actually switched on.

        Configured *and* enabled: `SSO_CONNECTIONS` can only narrow what the
        project offers, never add to it. If the project has never answered,
        the configuration stands - a sign-in page with no buttons over a
        transient blip would be worse than one button that might fail.
        """
        enabled = await self._auth.enabled_connections()
        if enabled is None:
            return self._connections
        return tuple(connection for connection in self._connections if connection in enabled)

    def authorize(self, *, connection: Connection, redirect_uri: str) -> AuthorizationRequest:
        if connection not in self._connections:
            raise ValueError(f"supabase is not configured to broker {connection!r}")

        state = secrets.token_urlsafe(ENTROPY_BYTES)
        verifier = secrets.token_urlsafe(ENTROPY_BYTES)
        url = self._auth.authorize_url(
            connection=connection,
            redirect_to=_with_query(redirect_uri, state=state),
            code_verifier=verifier,
        )
        # Supabase echoes no nonce, so one is not sent. The field is still
        # filled because the transaction record requires it; PKCE and `state`
        # are what bind the callback to this browser and this flow.
        return AuthorizationRequest(
            url=url,
            state=state,
            nonce=secrets.token_urlsafe(ENTROPY_BYTES),
            code_verifier=verifier,
        )

    async def exchange(
        self,
        *,
        code: str,
        code_verifier: str,
        nonce: str,
        redirect_uri: str,
    ) -> ProviderTokens:
        identity = await self._auth.exchange_code(auth_code=code, code_verifier=code_verifier)
        # Supabase's own tokens are not kept. This system issues its own session
        # (ADR 0022); holding a second, longer-lived credential for the same
        # person would be a thing to steal and nothing to use.
        return ProviderTokens(
            claims=identity.claims(),
            access_token=None,
            refresh_token=None,
            expires_at=None,
        )


def _with_query(url: str, **params: str) -> str:
    """`url` with `params` added to its query string."""
    parts = urlsplit(url)
    extra = urlencode(params)
    query = f"{parts.query}&{extra}" if parts.query else extra
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))
