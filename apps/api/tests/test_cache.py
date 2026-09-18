"""Caching: the keys, the policy, and the promise that work is done once.

Phase 15 is cheap to get subtly wrong - a key that omits a parameter serves the
answer to a different question, and a cache that "works" in a test because the
loader was called twice and both calls agreed proves nothing. So every test
here counts calls, and the keys are asserted against what actually decides the
answer.
"""

from __future__ import annotations

import asyncio

import pytest

from app.cache import (
    CacheNamespace,
    CachePolicy,
    InMemoryCache,
    NullCache,
    ResponseCache,
    build_cache,
    cache_key,
    disabled_cache,
    fingerprint,
)
from app.cache.single_flight import SingleFlight
from app.core.config import Settings


def policy(**overrides: object) -> CachePolicy:
    fields: dict[str, object] = {
        "ttl_seconds": dict.fromkeys(CacheNamespace, 60),
        "enabled": frozenset(CacheNamespace),
        "max_value_bytes": 1024 * 1024,
    }
    fields.update(overrides)
    return CachePolicy(**fields)  # type: ignore[arg-type]


def response_cache(**overrides: object) -> ResponseCache:
    return ResponseCache(InMemoryCache(), policy(**overrides))


class Counter:
    """A loader that says how many times it actually ran."""

    def __init__(self, value: object = "computed", *, delay: float = 0.0) -> None:
        self.calls = 0
        self._value = value
        self._delay = delay

    async def __call__(self) -> object:
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        return self._value


def echo(value: object) -> object:
    return value


# --- keys -----------------------------------------------------------------


def test_a_key_changes_when_any_input_does():
    """The failure this prevents: one key for two different questions."""
    base = cache_key(CacheNamespace.SEARCH, "tavily", "inference pricing", 10)

    assert base == cache_key(CacheNamespace.SEARCH, "tavily", "inference pricing", 10)
    assert base != cache_key(CacheNamespace.SEARCH, "brave", "inference pricing", 10)
    assert base != cache_key(CacheNamespace.SEARCH, "tavily", "inference pricing", 25)
    assert base != cache_key(CacheNamespace.PAGE, "tavily", "inference pricing", 10)


def test_a_key_is_stable_across_dict_ordering():
    """Canonical JSON, so a key does not depend on how a dict was built."""
    assert fingerprint({"b": 1, "a": 2}) == fingerprint({"a": 2, "b": 1})


def test_a_value_that_cannot_be_hashed_stably_is_refused():
    """A key built from `str(object)` would change with a dataclass field
    order and silently drop the whole cache. Better to fail at the call."""
    with pytest.raises(TypeError, match="cache key"):
        fingerprint(object())


def test_the_key_carries_a_schema_version():
    """Bumping it is the one invalidation that works across a half-old
    deployment: the two halves stop reading each other's entries."""
    assert ":v1:" in cache_key(CacheNamespace.PAGE, "https://example.com")


# --- read-through ---------------------------------------------------------


async def test_the_second_caller_does_not_do_the_work_again():
    cache = response_cache()
    loader = Counter()

    first = await cache.through(CacheNamespace.SEARCH, "q", loader=loader, encode=echo, decode=echo)
    second = await cache.through(
        CacheNamespace.SEARCH, "q", loader=loader, encode=echo, decode=echo
    )

    assert loader.calls == 1
    assert (first.hit, second.hit) == (False, True)
    assert (first.origin, second.origin) == ("loader", "store")
    assert second.value == "computed"


async def test_a_different_key_is_a_different_answer():
    cache = response_cache()
    loader = Counter()

    await cache.through(CacheNamespace.SEARCH, "a", loader=loader, encode=echo, decode=echo)
    await cache.through(CacheNamespace.SEARCH, "b", loader=loader, encode=echo, decode=echo)

    assert loader.calls == 2


async def test_an_entry_that_cannot_be_decoded_is_dropped_rather_than_raised():
    """The stale-encoding case: a value written by an older build. Recomputing
    is always available; failing the run is not acceptable."""
    cache = response_cache()
    loader = Counter("fresh")

    def refuse(raw: object) -> object:
        raise ValueError("this encoding is from another version")

    await cache.through(CacheNamespace.SEARCH, "q", loader=loader, encode=echo, decode=echo)
    again = await cache.through(
        CacheNamespace.SEARCH, "q", loader=loader, encode=echo, decode=refuse
    )

    assert again.value == "fresh"
    assert loader.calls == 2


async def test_a_value_over_the_ceiling_is_not_stored():
    """A cache that accepts anything fills with the entries nobody reads."""
    cache = response_cache(max_value_bytes=64)
    loader = Counter("x" * 500)

    await cache.through(CacheNamespace.PAGE, "url", loader=loader, encode=echo, decode=echo)
    second = await cache.through(
        CacheNamespace.PAGE, "url", loader=loader, encode=echo, decode=echo
    )

    assert loader.calls == 2
    assert second.hit is False


async def test_an_unreachable_backend_is_a_miss_not_a_failure():
    """A research run must not fail because an optimisation is unavailable."""
    cache = ResponseCache(_BrokenBackend(), policy())
    loader = Counter()

    result = await cache.through(
        CacheNamespace.SEARCH, "q", loader=loader, encode=echo, decode=echo
    )

    assert result.value == "computed"
    assert loader.calls == 1


async def test_a_namespace_that_is_off_is_computed_every_time():
    cache = response_cache(enabled=frozenset({CacheNamespace.SEARCH}))
    loader = Counter()

    await cache.through(CacheNamespace.EMBEDDING, "t", loader=loader, encode=echo, decode=echo)
    await cache.through(CacheNamespace.EMBEDDING, "t", loader=loader, encode=echo, decode=echo)

    assert loader.calls == 2


async def test_invalidation_removes_one_entry():
    cache = response_cache()
    loader = Counter()
    await cache.through(CacheNamespace.PAGE, "url", loader=loader, encode=echo, decode=echo)

    await cache.invalidate(CacheNamespace.PAGE, "url")
    await cache.through(CacheNamespace.PAGE, "url", loader=loader, encode=echo, decode=echo)

    assert loader.calls == 2


async def test_stored_and_remember_are_the_batching_caller_s_pair():
    """What the embedding path uses: look each up, compute only the misses."""
    cache = response_cache()

    assert await cache.stored(CacheNamespace.EMBEDDING, "text", decode=echo) is None
    await cache.remember(CacheNamespace.EMBEDDING, "text", value=[0.1, 0.2], encode=echo)

    found = await cache.stored(CacheNamespace.EMBEDDING, "text", decode=echo)
    assert found is not None and found[0] == [0.1, 0.2]


# --- single-flight --------------------------------------------------------


async def test_simultaneous_identical_calls_are_done_once():
    """The fan-out case: two researchers given overlapping subtasks ask for the
    same URL in the same millisecond, and both miss the cache."""
    cache = response_cache()
    loader = Counter(delay=0.05)

    results = await asyncio.gather(
        *(
            cache.through(CacheNamespace.PAGE, "url", loader=loader, encode=echo, decode=echo)
            for _ in range(5)
        )
    )

    assert loader.calls == 1
    assert sum(1 for result in results if result.origin == "loader") == 1
    assert sum(1 for result in results if result.origin == "inflight") == 4
    assert all(result.value == "computed" for result in results)


async def test_deduplication_holds_even_with_caching_off():
    """Two different promises. Only remembering the answer is optional."""
    cache = disabled_cache()
    loader = Counter(delay=0.05)

    await asyncio.gather(
        *(
            cache.through(CacheNamespace.PAGE, "url", loader=loader, encode=echo, decode=echo)
            for _ in range(4)
        )
    )

    assert loader.calls == 1


async def test_a_failure_is_shared_by_everyone_who_joined_it():
    """They would each have made the same request to the same broken host."""
    flight = SingleFlight()
    attempts = 0

    async def failing() -> object:
        nonlocal attempts
        attempts += 1
        await asyncio.sleep(0.05)
        raise RuntimeError("the host is down")

    outcomes = await asyncio.gather(
        *(flight.run("k", failing) for _ in range(3)), return_exceptions=True
    )

    assert attempts == 1
    assert all(isinstance(outcome, RuntimeError) for outcome in outcomes)
    assert not flight.is_inflight("k"), "the key is released so the next caller retries"


async def test_a_failed_call_is_not_remembered_as_an_answer():
    cache = response_cache()
    calls = 0

    async def flaky() -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient")
        return "second time lucky"

    with pytest.raises(RuntimeError):
        await cache.through(CacheNamespace.SEARCH, "q", loader=flaky, encode=echo, decode=echo)
    result = await cache.through(CacheNamespace.SEARCH, "q", loader=flaky, encode=echo, decode=echo)

    assert result.value == "second time lucky"


# --- the backends ---------------------------------------------------------


async def test_the_in_memory_backend_expires_entries():
    backend = InMemoryCache()
    await backend.set("k", "v", ttl_seconds=0)

    assert await backend.get("k") is None


async def test_the_in_memory_backend_is_bounded():
    """These values are pages and vectors: an unbounded dict of them is a leak
    that shows up as a worker whose memory grows over days."""
    backend = InMemoryCache(max_entries=3)
    for index in range(6):
        await backend.set(f"k{index}", "v", ttl_seconds=60)

    survivors = [index for index in range(6) if await backend.get(f"k{index}") is not None]
    assert survivors == [3, 4, 5]


async def test_the_null_backend_never_stores():
    backend = NullCache()
    await backend.set("k", "v", ttl_seconds=60)

    assert await backend.get("k") is None


# --- configuration --------------------------------------------------------


def test_caching_can_be_turned_off_entirely():
    cache = build_cache(Settings(app_env="test", cache_enabled=False))

    assert cache.policy.enabled == frozenset()


def test_embeddings_can_be_excluded_without_losing_the_rest():
    """A deployment that would rather not cache a function of user documents
    at all keeps the public caches and pays for the vectors again."""
    cache = build_cache(Settings(app_env="test", cache_embeddings=False))

    assert CacheNamespace.EMBEDDING not in cache.policy.enabled
    assert CacheNamespace.SEARCH in cache.policy.enabled


def test_nothing_belonging_to_one_person_has_a_namespace_to_live_in():
    """The project rule, kept structurally: there is nowhere to put it.

    Retrieval results, evidence and reports are per user or per run. This test
    fails if a namespace for one of them is ever added without the argument
    that would have to come with it.
    """
    assert {namespace.value for namespace in CacheNamespace} == {
        "search",
        "page",
        "extract",
        "embedding",
    }


class _BrokenBackend:
    """A cache backend that is down."""

    async def get(self, key: str) -> str | None:
        return None

    async def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        raise ConnectionError("redis is unreachable")

    async def delete(self, *keys: str) -> None:
        raise ConnectionError("redis is unreachable")

    async def close(self) -> None:
        return None
