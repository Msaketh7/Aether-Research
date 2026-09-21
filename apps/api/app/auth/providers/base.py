"""The identity-provider seam.

Auth0 and Supabase both broker the same thing - a Google or GitHub sign-in -
and the rest of the system must not be able to tell which one did it. This is
the interface that makes that true, and it is the same shape the project uses
for every other replaceable dependency: models, storage, search, retrieval
(ADR 0003). A provider is constructed from settings, registered by name, and
never imported by the code that uses it.

**What a provider is responsible for**: speaking OAuth 2.0 and OIDC to one
upstream, and returning a verified, normalised identity. Nothing else. It does
not touch the database, does not create users, does not issue this system's
tokens and does not know what a session is. That keeps the part that varies
per vendor small enough to read, and it means the linking rules - which are
where the security decisions actually live - have one implementation rather
than one per provider.

**What a provider must never do**: return claims it has not verified. Every
implementation of :meth:`IdentityProvider.exchange` returns
:class:`ProviderClaims` only after checking the ID token's signature against
the provider's published keys, its issuer, its audience, its expiry and the
nonce this system generated. A provider that returns unverified claims moves
an authentication bypass behind an interface that looks safe.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from app.core.errors import AppError

#: The upstream connections a provider can broker. A closed set: each one is a
#: button on the sign-in page and a branch in the provider configuration, and
#: an open set would let a request name a connection nobody configured.
Connection = Literal["google", "github"]

#: The providers this system knows how to build. `local` is not here because a
#: first-party password check is not an OAuth provider and shares none of this
#: interface - it issues tokens directly.
ProviderName = Literal["auth0", "supabase"]


class ProviderError(AppError):
    """An identity provider could not complete the flow.

    Deliberately a 502 rather than a 401: the caller's credentials were not
    refused, an upstream this system depends on failed. Reporting it as an
    authentication failure would send people to re-enter passwords over an
    outage they cannot do anything about, and would hide a broken provider
    configuration behind what looks like ordinary user error.
    """

    status_code = 502
    code = "provider_unavailable"


class ProviderRefused(AppError):
    """The provider declined, or the user declined at the provider.

    Separate from :class:`ProviderError` because the response differs: there is
    nothing to retry and nothing broken. The user pressed cancel, or the
    upstream rejected the request for a reason that will not change on a second
    attempt.
    """

    status_code = 401
    code = "access_denied"


@dataclass(frozen=True, slots=True)
class ProviderClaims:
    """A verified identity, normalised across providers.

    Every field has been checked against a signature by the time this exists.
    """

    #: The provider's stable identifier for this person. **This is the join
    #: key**, not the email. An address can be changed at the provider, reused
    #: by a different person after an account is deleted, or simply not be
    #: unique across the connections one provider brokers; a subject is none of
    #: those things. Linking on email is the classic account-takeover bug in
    #: federated sign-in and it is not available through this type.
    subject: str

    #: Which upstream the provider brokered, when it says. `None` when the
    #: provider does not report it, which is not an error - it is only used to
    #: label the identity in the UI.
    connection: Connection | None

    email: str | None
    #: Whether the provider asserts it has verified the address. Never assumed.
    #: An unverified address is an address somebody typed, and treating it as
    #: proof of ownership lets anyone claim an account by typing its email at a
    #: provider that does not check.
    email_verified: bool

    name: str | None = None

    @property
    def usable_email(self) -> str | None:
        """The address, only if the provider verified it."""
        return self.email if (self.email and self.email_verified) else None


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """Where to send the browser, and what must be remembered until it returns."""

    #: The provider's authorization endpoint with every parameter applied.
    url: str
    #: Held server-side and compared on the way back. Never sent to the browser
    #: in a readable form.
    state: str
    nonce: str
    code_verifier: str


@dataclass(frozen=True, slots=True)
class ProviderTokens:
    """What an authorization code was exchanged for.

    The refresh token is optional because not every provider issues one, and
    whether it does depends on scopes the deployment configures rather than on
    anything this code controls.
    """

    claims: ProviderClaims
    access_token: str | None
    refresh_token: str | None
    expires_at: dt.datetime | None


@runtime_checkable
class IdentityProvider(Protocol):
    """One brokered identity provider.

    `runtime_checkable` so the registry can assert what it built satisfies this
    without importing every implementation.
    """

    @property
    def name(self) -> ProviderName: ...

    @property
    def connections(self) -> tuple[Connection, ...]:
        """Which upstreams this provider is configured to broker.

        A property rather than a constant: a deployment enables Google and
        GitHub independently, and a button for a connection the tenant has not
        configured is a dead end.
        """
        ...

    def authorize(
        self,
        *,
        connection: Connection,
        redirect_uri: str,
    ) -> AuthorizationRequest:
        """Build an authorization request.

        Synchronous on purpose: generating state, a nonce and a PKCE pair is
        local work, and making it `async` would suggest a round trip that does
        not happen.
        """
        ...

    async def exchange(
        self,
        *,
        code: str,
        code_verifier: str,
        nonce: str,
        redirect_uri: str,
    ) -> ProviderTokens:
        """Trade an authorization code for a **verified** identity.

        Raises :class:`ProviderRefused` when the upstream declines and
        :class:`ProviderError` when it cannot be reached or answers
        unusably. Never returns unverified claims.
        """
        ...
