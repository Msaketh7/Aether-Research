"""Where cached bytes actually live.

A deliberately small interface - get, set, delete - because everything
interesting about caching is in the policy above it (``app.cache.store``) and
nothing interesting is in the storage. Three implementations:

* ``RedisCache`` for a deployment, where several workers and API replicas share
  one cache and a TTL is the server's job;
* ``InMemoryCache`` for a test and for a single process with no Redis, bounded
  so that it cannot become a leak with a nicer name;
* ``NullCache``, which always misses. Not a degraded mode that happens by
  accident: it is what ``CACHE_ENABLED=false`` selects, and every caller is
  already correct without a cache.

A cache never raises. A backend that is down is a cache that misses, and a
research run must not fail because an optimisation is unavailable.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Protocol

from redis.asyncio import Redis

from app.core.logging import get_logger

logger = get_logger(__name__)


class Cache(Protocol):
    """Bytes in, bytes out, with an expiry."""

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None: ...

    async def delete(self, *keys: str) -> None: ...

    async def close(self) -> None: ...


class NullCache:
    """Always misses, never stores. What "caching off" means."""

    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        return None

    async def delete(self, *keys: str) -> None:
        return None

    async def close(self) -> None:
        return None


class InMemoryCache:
    """A bounded LRU with per-entry expiry, for one process.

    Bounded because the values here are pages and vectors: an unbounded dict of
    them is the same leak as holding every page a run has ever fetched, and it
    would appear as a worker whose memory grows over days.
    """

    def __init__(self, *, max_entries: int = 2048) -> None:
        self._max_entries = max_entries
        self._entries: OrderedDict[str, tuple[float, str]] = OrderedDict()

    async def get(self, key: str) -> str | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= time.monotonic():
            del self._entries[key]
            return None
        self._entries.move_to_end(key)
        return value

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        self._entries[key] = (time.monotonic() + ttl_seconds, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self._entries.pop(key, None)

    async def close(self) -> None:
        self._entries.clear()


class RedisCache:
    """The shared cache. One key per entry, expiry set by the server.

    Failures are swallowed and logged rather than raised. A cache is an
    optimisation, and the one behaviour that is never acceptable is a research
    run failing because the optimisation was unavailable - which is also why
    every caller treats a miss and an outage identically.
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get(self, key: str) -> str | None:
        try:
            value = await self._redis.get(key)
        except Exception as exc:
            logger.warning("cache read failed", extra={"error": str(exc)})
            return None
        return None if value is None else str(value)

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        try:
            await self._redis.set(key, value, ex=ttl_seconds)
        except Exception as exc:
            logger.warning("cache write failed", extra={"error": str(exc)})

    async def delete(self, *keys: str) -> None:
        if not keys:
            return
        try:
            await self._redis.delete(*keys)
        except Exception as exc:
            logger.warning("cache delete failed", extra={"error": str(exc)})

    async def close(self) -> None:
        await self._redis.aclose()
