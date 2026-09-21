"""The auth cookies' attributes, decided in one place.

Three cookies, each with a different job and therefore different attributes.
Every attribute below is a security control rather than a preference:

* ``HttpOnly`` - script cannot read them, so an XSS bug on the frontend cannot
  exfiltrate a token. Not negotiable and therefore not configurable. This is
  also why the tokens live in cookies at all rather than in `localStorage`,
  which is readable by any script that runs on the page.
* ``Secure`` - never sent over plain HTTP. Configurable only because local
  development is served over HTTP, and the resolution defaults to *on*
  everywhere but ``local`` and ``test``.
* ``SameSite`` - ``Lax`` by default, which is enough whenever the web app and
  the API share a registrable domain. A deployment that genuinely splits them
  needs ``none``, which browsers only honour together with ``Secure`` - so that
  combination is validated rather than discovered in production.
* ``Path`` - the one attribute that differs between them, and deliberately.

**Why the refresh cookie is path-scoped.** The access token is sent with every
API request because every request needs it. The refresh token is needed by
exactly one endpoint, so it is scoped to that endpoint's path and the browser
sends it nowhere else. That is a real reduction in exposure: the credential
that can mint new sessions is absent from the hundreds of requests that have no
use for it, so a logging mistake or a misdirected proxy cannot capture it.

**Why the OAuth transaction cookie is ``Lax`` and never ``Strict``.** It is
read on the callback, which arrives as a top-level navigation *from the
provider's origin*. ``Strict`` withholds cookies on exactly that kind of
cross-site navigation, so a ``Strict`` transaction cookie is simply absent when
the callback runs, and every single sign-in fails with what looks like a
mismatched state. ``Lax`` sends it on top-level GET navigations, which is what
this is. This is not a weakening: the cookie's job is to prove the callback
reached the same browser, and `state` is checked alongside it.

Deletion repeats the attributes, because a browser matches a removal against
name, domain and path: a ``Set-Cookie`` that clears the wrong triple leaves the
original in place and the logout does nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from starlette.responses import Response

from app.core.config import Settings

SameSite = Literal["lax", "strict", "none"]

#: Where the refresh cookie is sent, and nowhere else. Must match the mounted
#: path of the refresh endpoint; a mismatch means the browser never sends it
#: and every renewal fails.
REFRESH_COOKIE_PATH = "/api/v1/auth/refresh"


@dataclass(frozen=True, slots=True)
class CookiePolicy:
    """Everything that goes on the ``Set-Cookie`` lines except the values."""

    name: str
    access_max_age: int
    refresh_max_age: int
    secure: bool
    samesite: SameSite
    transaction_max_age: int
    domain: str | None = None
    path: str = "/"

    @classmethod
    def from_settings(cls, settings: Settings) -> CookiePolicy:
        return cls(
            name=settings.session_cookie_name,
            access_max_age=settings.access_token_ttl_seconds,
            refresh_max_age=settings.session_ttl_seconds,
            secure=settings.session_cookie_is_secure,
            samesite=settings.session_cookie_samesite,
            transaction_max_age=settings.sso_transaction_ttl_seconds,
            domain=settings.session_cookie_domain,
        )

    @property
    def access_name(self) -> str:
        return f"{self.name}_at"

    @property
    def refresh_name(self) -> str:
        return f"{self.name}_rt"

    @property
    def transaction_name(self) -> str:
        # Not derived from `name`: it is not a session, it is a ten-minute
        # handle to a half-finished sign-in, and a distinct name keeps the two
        # from being confused in a browser's cookie jar or in a log.
        return "aether_oauth"

    def attach_tokens(self, response: Response, *, access: str, refresh: str) -> None:
        """Set both token cookies on a response."""
        response.set_cookie(
            key=self.access_name,
            value=access,
            max_age=self.access_max_age,
            path=self.path,
            domain=self.domain,
            secure=self.secure,
            httponly=True,
            samesite=self.samesite,
        )
        response.set_cookie(
            key=self.refresh_name,
            value=refresh,
            max_age=self.refresh_max_age,
            path=REFRESH_COOKIE_PATH,
            domain=self.domain,
            secure=self.secure,
            httponly=True,
            samesite=self.samesite,
        )

    def attach_transaction(self, response: Response, handle: str) -> None:
        """Set the short-lived handle naming a pending authorization."""
        response.set_cookie(
            key=self.transaction_name,
            value=handle,
            max_age=self.transaction_max_age,
            path="/",
            domain=self.domain,
            secure=self.secure,
            httponly=True,
            # See the module docstring: `strict` here breaks every sign-in.
            samesite="lax" if self.samesite == "strict" else self.samesite,
        )

    def clear_transaction(self, response: Response) -> None:
        response.delete_cookie(
            key=self.transaction_name,
            path="/",
            domain=self.domain,
            secure=self.secure,
            httponly=True,
            samesite="lax" if self.samesite == "strict" else self.samesite,
        )

    def clear(self, response: Response) -> None:
        """Remove both token cookies.

        Expressed as expired empty cookies with the same name, domain and path,
        which is the only form a browser treats as a deletion - and the refresh
        cookie must be cleared at *its* path, not at ``/``, or it survives the
        logout and can still mint tokens.
        """
        response.delete_cookie(
            key=self.access_name,
            path=self.path,
            domain=self.domain,
            secure=self.secure,
            httponly=True,
            samesite=self.samesite,
        )
        response.delete_cookie(
            key=self.refresh_name,
            path=REFRESH_COOKIE_PATH,
            domain=self.domain,
            secure=self.secure,
            httponly=True,
            samesite=self.samesite,
        )
