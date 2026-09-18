"""The session cookie's attributes, decided in one place.

A cookie is the whole client-side half of authentication, and every one of its
attributes is a security control:

* ``HttpOnly`` - script cannot read it, so an XSS bug on the frontend cannot
  exfiltrate the session. Not negotiable and therefore not configurable.
* ``Secure`` - never sent over plain HTTP. Configurable only because local
  development is served over HTTP, and the resolution defaults to *on*
  everywhere but ``local`` and ``test``.
* ``SameSite`` - ``Lax`` by default, which is enough whenever the web app and
  the API share a registrable domain (``app.example.com`` and
  ``api.example.com`` both being ``example.com``, and ``localhost:3000`` and
  ``localhost:8000`` both being ``localhost``). A deployment that genuinely
  puts them on different domains needs ``none``, which browsers only honour
  together with ``Secure`` - so that combination is validated rather than
  discovered in production.
* ``Path=/`` - the API is one surface; a narrower path would only mean the
  cookie silently missing from some of it.

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


@dataclass(frozen=True, slots=True)
class CookiePolicy:
    """Everything that goes on the ``Set-Cookie`` line except the value."""

    name: str
    max_age: int
    secure: bool
    samesite: SameSite
    domain: str | None = None
    path: str = "/"

    @classmethod
    def from_settings(cls, settings: Settings) -> CookiePolicy:
        return cls(
            name=settings.session_cookie_name,
            max_age=settings.session_ttl_seconds,
            secure=settings.session_cookie_is_secure,
            samesite=settings.session_cookie_samesite,
            domain=settings.session_cookie_domain,
        )

    def attach(self, response: Response, token: str) -> None:
        """Set the session cookie on a response."""
        response.set_cookie(
            key=self.name,
            value=token,
            max_age=self.max_age,
            path=self.path,
            domain=self.domain,
            secure=self.secure,
            httponly=True,
            samesite=self.samesite,
        )

    def clear(self, response: Response) -> None:
        """Remove the session cookie.

        Expressed as an expired empty cookie with the same name, domain and
        path, which is the only form a browser treats as a deletion.
        """
        response.delete_cookie(
            key=self.name,
            path=self.path,
            domain=self.domain,
            secure=self.secure,
            httponly=True,
            samesite=self.samesite,
        )
