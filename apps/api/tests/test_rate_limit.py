"""Rate limiting (Phase 20, TDD 3.2).

Two layers, tested separately because they fail differently. The bucket
arithmetic is a pure function of capacity, refill and elapsed time, so it is
tested directly with a clock the test controls - waiting for a real refill
would make the suite slow and flaky in exchange for testing nothing extra. The
wiring is tested over HTTP: which routes draw on which bucket, what a refused
request looks like, and what is deliberately not limited at all.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import create_app
from app.security.ratelimit import (
    AUTH,
    READ,
    WRITE,
    Decision,
    InMemoryRateLimitBackend,
    RateLimiter,
    Rule,
    bucket_key,
    rules_from_settings,
)
from tests.conftest import API, TEST_PASSWORD

# --- the bucket ----------------------------------------------------------


async def test_a_burst_is_allowed_up_to_the_capacity_and_then_refused():
    backend = InMemoryRateLimitBackend()
    rule = Rule("test", capacity=3, refill_per_second=1.0)

    outcomes = [
        (await backend.consume("k", rule=rule, cost=1, now=100.0)).allowed for _ in range(4)
    ]

    assert outcomes == [True, True, True, False]


async def test_a_refusal_says_when_to_come_back_and_never_says_now():
    """`Retry-After: 0` is an invitation to retry immediately, which is the
    behaviour being refused."""
    backend = InMemoryRateLimitBackend()
    rule = Rule("test", capacity=1, refill_per_second=0.5)

    await backend.consume("k", rule=rule, cost=1, now=100.0)
    decision = await backend.consume("k", rule=rule, cost=1, now=100.0)

    assert not decision.allowed
    assert decision.retry_after_seconds >= 1
    assert decision.limit == 1


async def test_tokens_come_back_at_the_refill_rate():
    backend = InMemoryRateLimitBackend()
    rule = Rule("test", capacity=2, refill_per_second=1.0)

    for _ in range(2):
        await backend.consume("k", rule=rule, cost=1, now=100.0)
    assert not (await backend.consume("k", rule=rule, cost=1, now=100.0)).allowed

    assert (await backend.consume("k", rule=rule, cost=1, now=101.0)).allowed


async def test_a_bucket_never_refills_past_its_capacity():
    """Otherwise an idle client accumulates an unbounded burst, and the
    capacity stops being a ceiling on anything."""
    backend = InMemoryRateLimitBackend()
    rule = Rule("test", capacity=2, refill_per_second=1.0)

    await backend.consume("k", rule=rule, cost=1, now=100.0)
    allowed = [
        (await backend.consume("k", rule=rule, cost=1, now=10_000.0)).allowed for _ in range(3)
    ]

    assert allowed == [True, True, False]


async def test_a_clock_that_goes_backwards_does_not_grant_tokens():
    """Replicas have their own clocks. Skew must be able to cost a client
    tokens, never mint them."""
    backend = InMemoryRateLimitBackend()
    rule = Rule("test", capacity=1, refill_per_second=1.0)

    await backend.consume("k", rule=rule, cost=1, now=100.0)

    assert not (await backend.consume("k", rule=rule, cost=1, now=50.0)).allowed


async def test_identities_and_rules_have_separate_buckets():
    limiter = RateLimiter(
        InMemoryRateLimitBackend(),
        {
            READ: Rule(READ, capacity=1, refill_per_second=0.01),
            WRITE: Rule(WRITE, capacity=1, refill_per_second=0.01),
        },
    )

    assert (await limiter.check(READ, "user:a")).allowed
    assert not (await limiter.check(READ, "user:a")).allowed
    # A different person, and a different class of route for the same person.
    assert (await limiter.check(READ, "user:b")).allowed
    assert (await limiter.check(WRITE, "user:a")).allowed


async def test_the_in_memory_backend_is_bounded():
    """The key is derived from a client address, so an unbounded map keyed by
    it is a memory-exhaustion bug wearing a rate limiter's clothes."""
    backend = InMemoryRateLimitBackend(max_entries=10)
    rule = Rule("test", capacity=1, refill_per_second=1.0)

    for index in range(100):
        await backend.consume(f"k{index}", rule=rule, cost=1, now=100.0)

    assert len(backend._buckets) == 10


def test_the_key_does_not_contain_the_identity():
    """The identity can be an email address or a client IP. Neither belongs in
    a key that shows up in a keyspace listing or a slow-log entry."""
    key = bucket_key(AUTH, "email:ada@example.com")

    assert "ada@example.com" not in key
    assert key.startswith("rl:")


async def test_a_disabled_limiter_allows_everything():
    limiter = RateLimiter(
        InMemoryRateLimitBackend(),
        {READ: Rule(READ, capacity=1, refill_per_second=0.01)},
        enabled=False,
    )

    allowed = [(await limiter.check(READ, "user:a")).allowed for _ in range(10)]

    assert allowed == [True] * 10


async def test_a_backend_that_cannot_answer_fails_open():
    """A deliberate trade, recorded in the threat model: an outage of the
    limiter's backend must not become an outage of the product. The headers
    must then report a full bucket rather than an empty one, so a client is not
    told it is nearly out of an allowance nobody is counting."""

    class Broken:
        async def consume(self, key: str, **_: object) -> Decision | None:
            return None

        async def close(self) -> None:
            return None

    limiter = RateLimiter(Broken(), {READ: Rule(READ, capacity=5, refill_per_second=1.0)})

    decision = await limiter.check(READ, "user:a")

    assert decision.allowed
    assert decision.remaining == 5


def test_the_configured_rules_match_the_settings():
    settings = Settings(app_env="test", rate_limit_read_per_minute=600, rate_limit_read_burst=7)

    rules = rules_from_settings(settings)

    assert rules[READ].capacity == 7
    assert rules[READ].per_minute == 600
    assert rules[READ].refill_per_second == 10.0


# --- the wiring -----------------------------------------------------------


def _app_with_limits(settings: Settings, **overrides: object) -> object:
    return create_app(settings.model_copy(update=overrides))


@pytest.fixture
async def tight_client(strict_settings: Settings):
    """A client whose read allowance is three requests and refills slowly."""
    app = _app_with_limits(
        strict_settings,
        rate_limit_read_burst=3,
        rate_limit_read_per_minute=1,
        rate_limit_auth_burst=2,
        rate_limit_auth_per_minute=1,
    )
    async with (
        AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as http,
        app.router.lifespan_context(app),
    ):
        yield http


async def test_the_whole_versioned_surface_is_limited(tight_client: AsyncClient):
    """Attached once to the router rather than per endpoint: a new route must
    not be unlimited until somebody remembers to decorate it."""
    statuses = [(await tight_client.get(f"{API}/research")).status_code for _ in range(4)]

    assert statuses[-1] == 429


async def test_a_refused_request_carries_the_headers_a_client_can_act_on(
    tight_client: AsyncClient,
):
    for _ in range(4):
        response = await tight_client.get(f"{API}/research")

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert int(response.headers["retry-after"]) >= 1
    assert response.headers["x-ratelimit-limit"] == "3"


async def test_health_probes_are_not_limited(tight_client: AsyncClient):
    """A load balancer's health check is not an API client, and throttling it
    is how a healthy replica gets taken out of service."""
    statuses = [(await tight_client.get("/health")).status_code for _ in range(10)]

    assert set(statuses) == {200}


async def test_the_credential_endpoints_are_limited_by_the_address_attempted(
    tight_client: AsyncClient,
):
    """The control an address-only limit misses: many clients, one account,
    which is what credential stuffing looks like."""
    attempt = {"email": "victim@example.com", "password": "wrong-password-guess"}

    statuses = []
    for _ in range(3):
        response = await tight_client.post(f"{API}/auth/login", json=attempt)
        statuses.append(response.status_code)

    assert statuses[:2] == [401, 401]
    assert statuses[2] == 429


async def test_the_credential_limit_is_spent_before_the_password_is_checked(
    tight_client: AsyncClient,
):
    """A refusal must cost the attacker a round trip and cost this process no
    Argon2 - otherwise the rate limit protects the account while the hash
    protects nobody from the CPU bill."""
    for _ in range(2):
        await tight_client.post(
            f"{API}/auth/login", json={"email": "victim@example.com", "password": TEST_PASSWORD}
        )

    response = await tight_client.post(
        f"{API}/auth/login",
        # A password long enough to be expensive, if it were ever hashed.
        json={"email": "victim@example.com", "password": "x" * 1024},
    )

    assert response.status_code == 429
