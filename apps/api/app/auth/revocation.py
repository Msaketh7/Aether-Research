"""Which sessions have been signed out, for tokens that are still valid.

An access token is checked by signature, so it keeps working until it expires
however loudly the database disagrees. This is the index that closes that gap:
signing a device out writes its session id here, and every verification
consults it, so the refusal lands on the next request rather than up to fifteen
minutes later.

**Not a cache, and deliberately not in `app.cache`.** That module's namespace
list is a closed set of *public, derivable* values - a search answer, a fetched
page, an embedding - and its own docstring rules out putting a system of record
behind it. This is the opposite kind of thing: it is authoritative, a miss is
not recoverable by recomputing, and a wrongly-dropped entry means a session
somebody revoked starts working again. It also must never evict, which the
bounded in-memory cache does by design.

**What a lost entry actually costs.** If this store is emptied - a Redis
restart, a process restart in development - revocation degrades to the access
token's remaining lifetime, and no further. It cannot degrade past that,
because the refresh flow reads `sessions.revoked_at` from Postgres rather than
from here, so a revoked session cannot be renewed regardless of what this
store remembers. Postgres is the durable record of a revocation; this is what
makes it take effect before the token would have expired anyway.

**Entries expire, so this does not grow.** A revocation is only interesting
while a token carrying that session id could still verify. Past that the token
fails on `exp` and the entry is redundant, so every write carries a TTL of the
access-token lifetime plus the clock skew allowance.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import uuid
from typing import Protocol

from app.core.logging import get_logger

logger = get_logger(__name__)

#: Key prefix, versioned so a change of meaning cannot collide with entries
#: written by an older build sharing the same Redis.
KEY_PREFIX = "aether:revoked:v1"


def revocation_key(session_id: uuid.UUID) -> str:
    return f"{KEY_PREFIX}:{session_id}"


class RevocationStore(Protocol):
    """The two operations an index needs."""

    async def revoke(self, session_id: uuid.UUID, *, ttl_seconds: int) -> None: ...

    async def is_revoked(self, session_id: uuid.UUID) -> bool: ...


class InMemoryRevocationStore:
    """For `APP_ENV=test` and single-process development.

    Unbounded except by expiry, which is the one property that matters: a
    bounded store evicts, and an evicted revocation is a session that silently
    works again. Expired entries are swept on write, so the dictionary tracks
    live revocations rather than every session ever signed out.
    """

    def __init__(self) -> None:
        self._entries: dict[uuid.UUID, dt.datetime] = {}
        self._lock = asyncio.Lock()

    async def revoke(self, session_id: uuid.UUID, *, ttl_seconds: int) -> None:
        now = dt.datetime.now(dt.UTC)
        async with self._lock:
            self._entries = {sid: at for sid, at in self._entries.items() if at > now}
            self._entries[session_id] = now + dt.timedelta(seconds=ttl_seconds)

    async def is_revoked(self, session_id: uuid.UUID) -> bool:
        expires_at = self._entries.get(session_id)
        if expires_at is None:
            return False
        if expires_at <= dt.datetime.now(dt.UTC):
            # Past its usefulness: any token carrying this id has expired too.
            self._entries.pop(session_id, None)
            return False
        return True


class RedisRevocationStore:
    """The deployment's index, shared by the API and the worker.

    A `SET` with an expiry and a `EXISTS`, which is all this needs: the value
    carries no information, only the key's presence does.
    """

    def __init__(self, redis: object) -> None:
        self._redis = redis

    async def revoke(self, session_id: uuid.UUID, *, ttl_seconds: int) -> None:
        await self._redis.set(  # type: ignore[attr-defined]
            revocation_key(session_id), "1", ex=max(1, ttl_seconds)
        )

    async def is_revoked(self, session_id: uuid.UUID) -> bool:
        try:
            return bool(await self._redis.exists(revocation_key(session_id)))  # type: ignore[attr-defined]
        except Exception:
            # **Fail closed is wrong here, and this is the one place that
            # deserves saying out loud.** If Redis is unreachable, treating
            # every session as revoked signs out every user of the system over
            # an infrastructure blip. Treating none as revoked narrows
            # revocation to the access token's remaining lifetime, which is the
            # guarantee the design already makes when this store is empty - and
            # the refresh flow still refuses from Postgres. So the degraded
            # mode is the documented one rather than a total outage.
            logger.error(
                "revocation index unreachable; revocation falls back to token expiry",
                extra={"session_id": str(session_id)},
            )
            return False


def revocation_ttl_seconds(access_token_ttl_seconds: int, *, skew_seconds: int = 60) -> int:
    """How long a revocation must be remembered.

    Exactly as long as a token carrying that session id could still verify: its
    lifetime, plus the clock-skew allowance a verifier grants. A shorter TTL
    would let a token outlive its own revocation.
    """
    return access_token_ttl_seconds + skew_seconds
