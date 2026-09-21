"""The tokens this system mints, and what makes them revocable.

Since single sign-on replaced opaque sessions (ADR 0022), a caller proves who
they are with a signed access token rather than with a row lookup. That buys
stateless validation and costs the one property opaque sessions gave away for
free: a token stays valid until it expires, whatever the database says.

This module is where that cost is paid back, and the three parts are not
separable:

1. **The access token is short-lived** (fifteen minutes by default). It is a
   bearer credential validated by signature alone, so its lifetime *is* the
   window in which a stolen one works.
2. **The refresh token is opaque, stored hashed, and rotated on every use.**
   Opaque because there is nothing to gain from it being self-describing and
   everything to lose - a long-lived signed token is a long-lived bypass.
   Rotated because a refresh token that never changes is a password with
   worse handling.
3. **Reuse of a rotated refresh token revokes the whole family.** This is the
   part that turns rotation from bookkeeping into a detector. A refresh token
   is used exactly once; a second use means two parties hold it, which means
   one of them stole it. Which one cannot be determined, so both are signed
   out and the person re-authenticates. Without this, rotation detects
   nothing - the thief simply rotates alongside the victim forever.

**`sid` is what makes revocation work.** Every access token carries the id of
the session that issued it, and validation consults a revocation index keyed
by that id. Signing a device out writes its `sid` there, and the next request
carrying a token from that session is refused - not in fifteen minutes, on the
next request. The index is small and bounded: an entry lives only until the
longest-lived token that could carry that `sid` has expired anyway, so it is
never a growing store of every session that ever existed.

The signing key is asymmetric (ES256) rather than a shared HMAC secret, for
the same reason the provider verification is asymmetric: the thing that
verifies a token should not be able to mint one. It also means the key can be
published for another service to verify against without that service becoming
able to issue.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import uuid
from dataclasses import dataclass

from joserfc import jwt
from joserfc.jwk import ECKey
from joserfc.jwt import JWTClaimsRegistry

from app.core.errors import Unauthenticated
from app.core.logging import get_logger

logger = get_logger(__name__)

#: The algorithm this system signs with, and the only one it will verify its
#: own tokens under. A single-element allowlist: see `oidc.ALLOWED_ALGORITHMS`
#: for why the list is fixed at verification rather than read from the token.
LOCAL_ALGORITHM = "ES256"

#: Clock skew tolerated when verifying a token this system signed. Smaller than
#: the allowance for a third party, because both ends are this deployment.
CLOCK_SKEW_SECONDS = 10

#: Bytes of entropy in a refresh token. The same 256 bits the opaque sessions
#: used, and for the same reason: it is guessed or it is not.
REFRESH_TOKEN_BYTES = 32

#: The longest token string that will be parsed at all. A real one is a few
#: hundred bytes; refusing early stops a caller handing the process arbitrary
#: parsing work.
MAX_TOKEN_LENGTH = 8192


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass(frozen=True, slots=True)
class AccessClaims:
    """A verified caller, as their access token describes them."""

    user_id: uuid.UUID
    #: The session this token was issued under. The revocation key.
    session_id: uuid.UUID
    #: This token's own id, so a single token can be traced through the logs.
    token_id: str
    email: str
    role: str
    #: Which issuer authenticated this person - `local`, `auth0`, `supabase`.
    #: Recorded so the audit log can say how somebody got in, and so a
    #: deployment can require a particular provider for a privileged action.
    provider: str
    issued_at: dt.datetime
    expires_at: dt.datetime


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    """An access token and the refresh token that will replace it.

    The refresh token appears in the clear exactly here and nowhere else; what
    is stored is its hash.
    """

    access_token: str
    access_expires_at: dt.datetime
    refresh_token: str
    refresh_expires_at: dt.datetime
    session_id: uuid.UUID


def mint_refresh_token() -> str:
    return secrets.token_urlsafe(REFRESH_TOKEN_BYTES)


def hash_refresh_token(token: str) -> str:
    """What is stored and what is looked up.

    SHA-256, not Argon2, for the reason the opaque session tokens used it: this
    is 256 bits of CSPRNG output, not a guessable secret, so a slow hash buys
    nothing and would add its cost to every refresh.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class TokenIssuer:
    """Signs and verifies this system's own access tokens."""

    def __init__(
        self,
        *,
        key: ECKey,
        issuer: str,
        audience: str,
        access_ttl_seconds: int,
    ) -> None:
        self._key = key
        self._issuer = issuer
        self._audience = audience
        self._access_ttl = dt.timedelta(seconds=access_ttl_seconds)

    @classmethod
    def from_pem(
        cls, pem: str | None, *, issuer: str, audience: str, access_ttl_seconds: int
    ) -> TokenIssuer:
        """Build from a configured PEM, or generate an ephemeral key.

        An ephemeral key is only ever correct in development: it is not shared
        with the worker process and it changes on restart, so every token
        minted before a reload stops verifying. The settings validator refuses
        an unset key outside `local` and `test` precisely so that this branch
        cannot be reached in a deployment.
        """
        if pem:
            key = ECKey.import_key(pem)
        else:
            logger.warning(
                "no JWT signing key configured; generating an ephemeral one. "
                "Every token is invalidated on restart - development only."
            )
            key = ECKey.generate_key("P-256")
        return cls(key=key, issuer=issuer, audience=audience, access_ttl_seconds=access_ttl_seconds)

    @property
    def public_jwk(self) -> dict[str, object]:
        """The verification key, for anything that needs to check a token.

        Public by construction - it cannot mint - which is the point of using
        an asymmetric algorithm for a first-party token in the first place.
        """
        return dict(self._key.as_dict(private=False))

    def issue_access_token(
        self,
        *,
        user_id: uuid.UUID,
        session_id: uuid.UUID,
        email: str,
        role: str,
        provider: str,
        now: dt.datetime | None = None,
    ) -> tuple[str, dt.datetime]:
        at = now or _now()
        expires_at = at + self._access_ttl

        claims: dict[str, object] = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": str(user_id),
            "sid": str(session_id),
            "jti": secrets.token_urlsafe(16),
            "iat": int(at.timestamp()),
            "nbf": int(at.timestamp()),
            "exp": int(expires_at.timestamp()),
            "email": email,
            "role": role,
            "provider": provider,
        }
        header = {"alg": LOCAL_ALGORITHM, "typ": "JWT", "kid": self._key.thumbprint()}
        return jwt.encode(header, claims, self._key, algorithms=[LOCAL_ALGORITHM]), expires_at

    def verify_access_token(self, token: str, *, now: dt.datetime | None = None) -> AccessClaims:
        """Check a token this system signed, and return who it names.

        Every failure raises the same :class:`Unauthenticated` with the same
        message. A caller holding an expired token, a forged one, one signed by
        a rotated key, or one for a different audience has exactly one correct
        next step - sign in again - and distinguishing them in the response
        only tells an attacker which part of their forgery to fix.
        """
        if not token or len(token) > MAX_TOKEN_LENGTH:
            raise Unauthenticated("Sign in to continue.", code="unauthenticated")

        try:
            decoded = jwt.decode(token, self._key, algorithms=[LOCAL_ALGORITHM])
        except Exception as exc:
            raise Unauthenticated("Sign in to continue.", code="unauthenticated") from exc

        registry = JWTClaimsRegistry(
            leeway=CLOCK_SKEW_SECONDS,
            iss={"essential": True, "value": self._issuer},
            aud={"essential": True, "value": self._audience},
            exp={"essential": True},
            sub={"essential": True},
        )
        try:
            registry.validate(decoded.claims)
        except Exception as exc:
            raise Unauthenticated("Sign in to continue.", code="unauthenticated") from exc

        claims = decoded.claims
        try:
            user_id = uuid.UUID(str(claims["sub"]))
            session_id = uuid.UUID(str(claims["sid"]))
        except (KeyError, ValueError) as exc:
            raise Unauthenticated("Sign in to continue.", code="unauthenticated") from exc

        expires_at = dt.datetime.fromtimestamp(int(claims["exp"]), tz=dt.UTC)
        issued_raw = claims.get("iat")
        issued_at = (
            dt.datetime.fromtimestamp(int(issued_raw), tz=dt.UTC)
            if isinstance(issued_raw, int | float)
            else expires_at - self._access_ttl
        )

        return AccessClaims(
            user_id=user_id,
            session_id=session_id,
            token_id=str(claims.get("jti", "")),
            email=str(claims.get("email", "")),
            # Defaulted rather than required. A token minted before a claim was
            # added must not become unverifiable when the claim appears; the
            # least-privileged value is the safe default for a missing role.
            role=str(claims.get("role", "user")),
            provider=str(claims.get("provider", "local")),
            issued_at=issued_at,
            expires_at=expires_at,
        )
