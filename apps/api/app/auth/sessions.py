"""Issuing, resolving and revoking sessions.

**Opaque server-side sessions, not JWTs** (ADR 0021). The token is 256 bits
from the operating system's CSPRNG and means nothing on its own; everything
about the session - who it belongs to, when it expires, whether it was revoked
- is a row. That is what makes "sign this device out" take effect on the next
request instead of whenever a signed token happens to expire.

**The token is hashed at rest with SHA-256, not with Argon2.** Password hashing
is slow on purpose because a password is low-entropy and guessable; a token
with 256 bits of entropy from a CSPRNG is not, so a slow hash would buy nothing
and would add its cost to *every authenticated request*. The property that
matters here is the same one either way: the database stores something that
cannot be replayed.

The hash is also the lookup key, so resolution is a single indexed equality
test. There is no secret-dependent comparison in this module to get wrong.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import uuid
from dataclasses import dataclass

from app.core.logging import get_logger
from app.db.models.user import SessionRow
from app.db.repositories.session import SessionRepository

logger = get_logger(__name__)

#: Bytes of entropy in a session token, before URL-safe encoding. 32 bytes is
#: 256 bits - far past the point where guessing is the weakest link.
TOKEN_BYTES = 32

#: Longest token string this will even hash. A session cookie is ~43
#: characters; anything longer is not one, and refusing early keeps a caller
#: from being able to hand the process arbitrary work.
MAX_TOKEN_LENGTH = 256


def mint_token() -> str:
    """A fresh session token. URL-safe, so it survives a cookie unencoded."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """What is stored and what is looked up. Never the token itself."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    """How long a session lives and how many a person may hold."""

    ttl_seconds: int
    max_per_user: int


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A session, plus the one and only time its token exists in the clear."""

    id: uuid.UUID
    token: str
    expires_at: dt.datetime


class SessionService:
    """The session lifecycle, over :class:`SessionRepository`."""

    def __init__(self, repository: SessionRepository, policy: SessionPolicy) -> None:
        self._repository = repository
        self._policy = policy

    async def issue(
        self, *, user_id: uuid.UUID, user_agent: str, ip: str | None, now: dt.datetime
    ) -> IssuedSession:
        """Start a session and return its token to be set as a cookie.

        The ceiling is applied *before* the new row is inserted, so the session
        being issued is never the one revoked to make room for itself.
        """
        if self._policy.max_per_user > 0:
            revoked = await self._repository.revoke_beyond(
                user_id, keeping=self._policy.max_per_user - 1, now=now
            )
            if revoked:
                logger.info(
                    "older sessions revoked to stay within the per-user ceiling",
                    extra={"revoked": revoked, "ceiling": self._policy.max_per_user},
                )

        token = mint_token()
        row = await self._repository.create(
            user_id=user_id,
            token_hash=hash_token(token),
            expires_at=now + dt.timedelta(seconds=self._policy.ttl_seconds),
            # Truncated here rather than at the column: a header is
            # attacker-controlled and its only use is being read by a person.
            user_agent=user_agent[:400],
            ip=ip,
        )
        return IssuedSession(id=row.id, token=token, expires_at=row.expires_at)

    async def resolve(self, token: str, *, now: dt.datetime) -> SessionRow | None:
        """The live session a token names, or nothing.

        A malformed, expired, revoked or simply unknown token are all the same
        answer. Nothing here distinguishes them, and nothing above it should:
        the caller's only correct response to any of them is 401.
        """
        if not token or len(token) > MAX_TOKEN_LENGTH:
            return None
        return await self._repository.find_active(hash_token(token), now=now)

    async def revoke(self, session_id: uuid.UUID, *, user_id: uuid.UUID, now: dt.datetime) -> bool:
        return await self._repository.revoke(session_id, user_id=user_id, now=now)

    async def revoke_others(
        self, *, user_id: uuid.UUID, keep: uuid.UUID | None, now: dt.datetime
    ) -> int:
        return await self._repository.revoke_all(user_id, now=now, keep=keep)

    async def list_active(self, user_id: uuid.UUID, *, now: dt.datetime) -> list[SessionRow]:
        return await self._repository.list_active(user_id, now=now)
