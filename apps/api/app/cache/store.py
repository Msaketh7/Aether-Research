"""The cache as a policy, not a dictionary (TDD 13).

Callers do not `get` and `set`. They say "produce this value, and here is what
decides it", and everything that makes caching safe happens in one place:

* **the key** is a content hash of the inputs (``app.cache.keys``);
* **the TTL** belongs to the namespace, so "search results are hours and
  embeddings are long" is one table rather than an argument at each call site;
* **the ceiling** refuses to store a value that is too large - a five-megabyte
  page is not worth a Redis key, and a cache that silently accepts one becomes
  the reason the cache is full;
* **simultaneous identical calls are done once** (``app.cache.single_flight``);
* **a failure is a miss.** Encoding, decoding and the backend are all wrapped:
  a cached value that cannot be read is dropped and recomputed, because the
  alternative is a research run that fails on a stale encoding.

What is *not* here is any notion of a user. Every namespace in the closed list
holds content the open internet served, or a deterministic function of it. A
value that belongs to one person - a retrieved passage from their upload, a
run's evidence, a report - is never cached, and the way that rule is kept is
that there is no namespace to put it in.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from app.cache.backend import Cache, NullCache
from app.cache.keys import CacheNamespace, cache_key
from app.cache.single_flight import SingleFlight
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Where a value came from. ``loader`` means this caller did the work.
Origin = Literal["store", "inflight", "loader"]

#: Told about every lookup, so a hit rate can be graphed (Phase 17). A plain
#: callback rather than a dependency on the metrics module: the cache has no
#: business knowing what a Prometheus counter is, and a test wants a list.
type CacheObserver = Callable[[CacheNamespace, Origin], None]


@dataclass(frozen=True, slots=True)
class Cached[T]:
    """A value, and whether producing it cost anything."""

    value: T
    origin: Origin

    @property
    def hit(self) -> bool:
        """True when this caller did not do the work.

        A coalesced call counts as a hit: no request was made for it either,
        and a ledger that recorded one would overstate what the run spent.
        """
        return self.origin != "loader"


@dataclass(frozen=True, slots=True)
class CachePolicy:
    """How long each namespace keeps a value, and which are on at all."""

    ttl_seconds: Mapping[CacheNamespace, int]
    enabled: frozenset[CacheNamespace]
    max_value_bytes: int

    def allows(self, namespace: CacheNamespace) -> bool:
        return namespace in self.enabled


class ResponseCache:
    """Read-through caching with content-hash keys and single-flight."""

    def __init__(
        self, cache: Cache, policy: CachePolicy, *, observer: CacheObserver | None = None
    ) -> None:
        self._cache = cache
        self._policy = policy
        self._flight = SingleFlight()
        self._observer = observer

    @property
    def policy(self) -> CachePolicy:
        return self._policy

    async def through[T](
        self,
        namespace: CacheNamespace,
        *parts: Any,
        loader: Callable[[], Awaitable[T]],
        encode: Callable[[T], Any],
        decode: Callable[[Any], T],
    ) -> Cached[T]:
        """The cached value for ``parts``, computing it once if it is missing.

        Single-flight runs even for a namespace whose caching is off: doing one
        piece of work once is a separate guarantee from remembering it, and the
        second is the one an operator might reasonably turn off.
        """
        key = cache_key(namespace, *parts)
        cacheable = self._policy.allows(namespace)

        if cacheable:
            stored = await self._read(key, decode)
            if stored is not None:
                return self._seen(namespace, Cached(stored[0], "store"))

        async def produce() -> T:
            value = await loader()
            if cacheable:
                await self._write(namespace, key, value, encode)
            return value

        value, ran = await self._flight.run(key, produce)
        return self._seen(namespace, Cached(value, "loader" if ran else "inflight"))

    async def stored[T](
        self, namespace: CacheNamespace, *parts: Any, decode: Callable[[Any], T]
    ) -> tuple[T] | None:
        """A value already cached, in a one-tuple, or ``None`` for a miss.

        For the caller that cannot use ``through`` because it batches: an
        embedding request is many texts, some known and some not, and the point
        is to send the provider only the ones that are not. Such a caller looks
        them up, computes the rest in one call, and ``remember``s each result.
        """
        if not self._policy.allows(namespace):
            return None
        return await self._read(cache_key(namespace, *parts), decode)

    async def remember[T](
        self, namespace: CacheNamespace, *parts: Any, value: T, encode: Callable[[T], Any]
    ) -> None:
        """Store one value the caller has just computed. The pair to ``stored``."""
        if not self._policy.allows(namespace):
            return
        await self._write(namespace, cache_key(namespace, *parts), value, encode)

    async def invalidate(self, namespace: CacheNamespace, *parts: Any) -> None:
        """Drop one entry. For a value the system itself knows has changed."""
        with contextlib.suppress(Exception):
            await self._cache.delete(cache_key(namespace, *parts))

    def _seen[T](self, namespace: CacheNamespace, cached: Cached[T]) -> Cached[T]:
        """Tell the observer, and never let it interfere.

        An optimisation that can be watched must not become one that can fail:
        a counter raising here would take down the call it was counting.
        """
        if self._observer is not None:
            try:
                self._observer(namespace, cached.origin)
            except Exception:
                logger.debug("a cache observer raised", extra={"namespace": namespace.value})
        return cached

    # --- internals --------------------------------------------------------

    async def _read[T](self, key: str, decode: Callable[[Any], T]) -> tuple[T] | None:
        """The stored value in a one-tuple, or ``None`` for a miss.

        A tuple rather than the value, because ``None`` is a legitimate cached
        value for some callers and would otherwise read as a miss.
        """
        try:
            raw = await self._cache.get(key)
            if raw is None:
                return None
            return (decode(json.loads(raw)),)
        except Exception as exc:
            # A value written by an older encoding, or a truncated one. Drop it
            # rather than fail: the loader produces a correct value, and the
            # next write replaces what could not be read.
            logger.warning(
                "a cache entry could not be read",
                extra={"key": key, "error": str(exc)},
            )
            with contextlib.suppress(Exception):
                await self._cache.delete(key)
            return None

    async def _write[T](
        self, namespace: CacheNamespace, key: str, value: T, encode: Callable[[T], Any]
    ) -> None:
        try:
            payload = json.dumps(encode(value), separators=(",", ":"))
        except Exception as exc:
            logger.warning(
                "a value could not be encoded for the cache",
                extra={"namespace": namespace.value, "error": str(exc)},
            )
            return
        size = len(payload.encode("utf-8"))
        if size > self._policy.max_value_bytes:
            logger.debug(
                "a value was too large to cache",
                extra={
                    "namespace": namespace.value,
                    "bytes": size,
                    "limit": self._policy.max_value_bytes,
                },
            )
            return
        # The backends already swallow their own failures; this is the promise
        # kept at the layer that makes it, so no backend can break a run by
        # raising where a caller expects a cache.
        with contextlib.suppress(Exception):
            await self._cache.set(key, payload, ttl_seconds=self._policy.ttl_seconds[namespace])

    async def close(self) -> None:
        await self._cache.close()


def disabled_cache(*, observer: CacheObserver | None = None) -> ResponseCache:
    """A cache that stores nothing but still coalesces concurrent calls.

    What a caller gets when caching is off, so no call site needs a ``None``
    check and the deduplication guarantee holds either way.
    """
    return ResponseCache(
        NullCache(),
        CachePolicy(
            ttl_seconds=dict.fromkeys(CacheNamespace, 0),
            enabled=frozenset(),
            max_value_bytes=0,
        ),
        observer=observer,
    )
