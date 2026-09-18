"""Caching: don't pay twice for the same work (Phase 15, TDD 13).

Four things are cached, and each one is cached because it is *deterministic
given its inputs* and *expensive*: a search provider's answer to a query, a
fetched page, the readable article extracted from that page's HTML, and a
text's embedding vector. Everything else is not cached, and the closed
``CacheNamespace`` is how that stays true.

The rule that shapes the whole module is the project's: **never cache
sensitive per-user information globally.** None of the four namespaces holds
anything a person owns. Searches and pages are what the open internet served
anyone who asked. An extraction is a pure function of bytes already fetched.
An embedding is keyed by the hash of the exact text, so reading one requires
already having that text - the cache cannot tell a caller anything it did not
bring with it - and it can still be turned off for a deployment that would
rather not make that argument at all.

Deliberately absent: retrieval results, which are per run and where freshness
is the point (TDD 8.3); anything belonging to a run - claims, evidence,
reports - which are rows, and a cache in front of the system of record is a
second source of truth; and model completions other than embeddings, because
"deterministic sub-operation" is a claim about a prompt that nothing in this
codebase currently checks.
"""

from __future__ import annotations

from app.cache.backend import Cache, InMemoryCache, NullCache, RedisCache
from app.cache.keys import KEY_VERSION, CacheNamespace, cache_key, fingerprint
from app.cache.single_flight import SingleFlight
from app.cache.store import Cached, CacheObserver, CachePolicy, ResponseCache, disabled_cache
from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

__all__ = [
    "KEY_VERSION",
    "Cache",
    "CacheNamespace",
    "CacheObserver",
    "CachePolicy",
    "Cached",
    "InMemoryCache",
    "NullCache",
    "RedisCache",
    "ResponseCache",
    "SingleFlight",
    "build_cache",
    "cache_key",
    "disabled_cache",
    "fingerprint",
]


def build_cache(settings: Settings, *, observer: CacheObserver | None = None) -> ResponseCache:
    """The cache this process should use.

    Redis everywhere except tests, exactly as the queue and the event bus are
    chosen: a shared cache is the only kind that helps, since the point is that
    a page one worker fetched is a page another worker does not have to. The
    in-memory backend under ``APP_ENV=test`` keeps the suite hermetic and still
    exercises every policy above the backend.

    ``CACHE_ENABLED=false`` returns a cache that stores nothing, and still
    coalesces simultaneous identical calls - those are two different promises,
    and only the first is an optimisation an operator might want to switch off.
    """
    if not settings.cache_enabled:
        logger.info("response caching is disabled")
        return disabled_cache(observer=observer)

    backend: Cache
    if settings.app_env == "test":
        backend = InMemoryCache(max_entries=settings.cache_max_entries)
    else:
        from app.workers.queue import build_redis

        backend = RedisCache(build_redis(settings.redis_url))

    enabled = {
        CacheNamespace.SEARCH,
        CacheNamespace.PAGE,
        CacheNamespace.EXTRACT,
    }
    if settings.cache_embeddings:
        enabled.add(CacheNamespace.EMBEDDING)

    policy = CachePolicy(
        ttl_seconds={
            CacheNamespace.SEARCH: settings.cache_search_ttl_seconds,
            CacheNamespace.PAGE: settings.cache_page_ttl_seconds,
            CacheNamespace.EXTRACT: settings.cache_page_ttl_seconds,
            CacheNamespace.EMBEDDING: settings.cache_embedding_ttl_seconds,
        },
        enabled=frozenset(enabled),
        max_value_bytes=settings.cache_max_value_bytes,
    )
    logger.info(
        "response cache configured",
        extra={
            "backend": type(backend).__name__,
            "namespaces": sorted(namespace.value for namespace in enabled),
        },
    )
    return ResponseCache(backend, policy, observer=observer)
