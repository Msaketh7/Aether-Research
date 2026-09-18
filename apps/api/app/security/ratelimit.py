"""Rate limiting: a token bucket per identity and route class (TDD 3.2).

**Why a token bucket rather than a fixed window.** A fixed window of 60
requests a minute lets a client send 60 at 00:59 and 60 more at 01:00 - 120 in
two seconds, which is the burst the limit existed to prevent. A bucket has two
numbers that mean different things: *capacity* is what a client may spend at
once after being idle, and *refill* is the sustained ceiling. Both are settings.

**Three classes, because the requests are not alike.** Reading a run is cheap.
Creating one starts a whole research job. Attempting a password is an
unauthenticated operation against a secret. Each has its own bucket, so heavy
reading cannot consume the allowance that stops a login being brute-forced.

**Identity is the user when there is one, and the client address when there is
not.** An authenticated client that changes address keeps its bucket, which is
the point; an unauthenticated one is limited by where it is calling from, which
is the only handle available. The address itself is resolved carefully - see
``app.security.forwarded``, because a client that can choose its own bucket key
has no limit at all.

**The limiter fails open.** A backend that is unreachable allows the request
and logs an error. This is a deliberate trade and it is the residual risk
recorded in the threat model: rate limiting protects against abuse, and an
outage of Redis turning into an outage of the product would be a worse failure
than a window of unlimited requests. The audit log still records what happened
during that window.
"""

from __future__ import annotations

import hashlib
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis

from app.core.config import Settings
from app.core.errors import RateLimited
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Bumped when the key layout changes, so a deployment's old buckets are
#: abandoned rather than misread.
KEY_VERSION = "v1"

#: Route classes. A closed set: a rule is chosen by name at the call site, and
#: a name that is not one of these is a typo that fails at import rather than a
#: request that is quietly unlimited.
READ = "read"
WRITE = "write"
AUTH = "auth"


@dataclass(frozen=True, slots=True)
class Rule:
    """One bucket's shape."""

    name: str
    #: Tokens the bucket holds. What a client may spend in one burst.
    capacity: int
    #: Tokens added per second. The sustained rate.
    refill_per_second: float

    @property
    def per_minute(self) -> int:
        return round(self.refill_per_second * 60)

    @property
    def ttl_seconds(self) -> int:
        """How long an untouched bucket is worth keeping.

        Once a full refill has elapsed the bucket is indistinguishable from a
        new one, so keeping it longer stores nothing. Plus a minute, so that a
        bucket is never dropped a moment before its owner comes back.
        """
        return math.ceil(self.capacity / max(self.refill_per_second, 1e-9)) + 60


@dataclass(frozen=True, slots=True)
class Decision:
    """What the bucket said, and everything the response headers need."""

    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int


class RateLimitBackend(Protocol):
    """Where the buckets live. Must be atomic: read-modify-write from two
    replicas is how a limit becomes a suggestion."""

    async def consume(self, key: str, *, rule: Rule, cost: int, now: float) -> Decision | None:
        """Spend ``cost`` tokens, or report that there were not enough.

        ``None`` means the backend could not answer. The caller allows the
        request; see the module docstring.
        """
        ...

    async def close(self) -> None: ...


def _decide(tokens: float, *, rule: Rule, cost: int, allowed: bool) -> Decision:
    """Turn a bucket level into the answer and its headers."""
    if allowed:
        return Decision(True, rule.capacity, int(tokens), 0)
    shortfall = cost - tokens
    return Decision(
        allowed=False,
        limit=rule.capacity,
        remaining=int(tokens),
        # At least a second: `Retry-After: 0` invites an immediate retry, which
        # is the behaviour being refused.
        retry_after_seconds=max(1, math.ceil(shortfall / rule.refill_per_second)),
    )


class InMemoryRateLimitBackend:
    """Buckets in this process. For tests and single-process development.

    Bounded, because a key is derived from a client address and an unbounded
    map keyed by attacker-controlled values is a memory exhaustion bug wearing
    a rate limiter's clothes. Evicting the least recently used bucket only ever
    grants tokens to somebody who stopped being the busiest caller.
    """

    def __init__(self, *, max_entries: int = 8192) -> None:
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()
        self._max_entries = max_entries

    async def consume(self, key: str, *, rule: Rule, cost: int, now: float) -> Decision:
        tokens, updated = self._buckets.get(key, (float(rule.capacity), now))
        tokens = min(rule.capacity, tokens + max(0.0, now - updated) * rule.refill_per_second)

        allowed = tokens >= cost
        if allowed:
            tokens -= cost

        self._buckets[key] = (tokens, now)
        self._buckets.move_to_end(key)
        while len(self._buckets) > self._max_entries:
            self._buckets.popitem(last=False)

        return _decide(tokens, rule=rule, cost=cost, allowed=allowed)

    async def close(self) -> None:
        self._buckets.clear()


#: The bucket, evaluated inside Redis.
#:
#: Atomic by being one script: refill and spend happen without another replica
#: interleaving between them, which a GET/SET pair could not promise. The
#: timestamp is the caller's, so replicas with clocks that disagree can grant
#: each other's clients up to (skew x refill) extra tokens - bounded, and the
#: alternative (reading Redis' own clock) costs portability across the versions
#: this may be deployed against.
_CONSUME_SCRIPT = """
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local now = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

local state = redis.call('HMGET', KEYS[1], 'tokens', 'at')
local tokens = tonumber(state[1])
local at = tonumber(state[2])
if tokens == nil or at == nil then
  tokens = capacity
  at = now
end

local elapsed = now - at
if elapsed < 0 then elapsed = 0 end
tokens = math.min(capacity, tokens + elapsed * refill)

local allowed = 0
if tokens >= cost then
  tokens = tokens - cost
  allowed = 1
end

redis.call('HSET', KEYS[1], 'tokens', tokens, 'at', now)
redis.call('EXPIRE', KEYS[1], ttl)
return {allowed, tostring(tokens)}
"""


class RedisRateLimitBackend:
    """The shared buckets. One script per decision, so the whole read-refill-
    spend cycle is atomic across every API replica."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._script = redis.register_script(_CONSUME_SCRIPT)

    async def consume(self, key: str, *, rule: Rule, cost: int, now: float) -> Decision | None:
        try:
            allowed, tokens = await self._script(
                keys=[key],
                args=[rule.capacity, rule.refill_per_second, cost, now, rule.ttl_seconds],
            )
        except Exception as exc:
            logger.error(
                "the rate limiter could not reach its backend; allowing the request",
                extra={"rule": rule.name, "error": str(exc)},
            )
            return None
        return _decide(float(tokens), rule=rule, cost=cost, allowed=bool(int(allowed)))

    async def close(self) -> None:
        await self._redis.aclose()


class RateLimiter:
    """The buckets this process enforces."""

    def __init__(
        self,
        backend: RateLimitBackend,
        rules: dict[str, Rule],
        *,
        enabled: bool = True,
    ) -> None:
        self._backend = backend
        self._rules = rules
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def rule(self, name: str) -> Rule:
        return self._rules[name]

    async def check(self, name: str, identity: str, *, cost: int = 1) -> Decision:
        """Spend from ``identity``'s bucket for route class ``name``.

        Returns the decision rather than raising, so that a caller can attach
        the headers on the way through whether or not the request was allowed.
        """
        rule = self._rules[name]
        if not self._enabled:
            return Decision(True, rule.capacity, rule.capacity, 0)

        key = bucket_key(rule.name, identity)
        decision = await self._backend.consume(key, rule=rule, cost=cost, now=time.time())
        if decision is None:
            # Failed open. Reported as a full bucket rather than an empty one:
            # the headers must not tell a client it is nearly out of an
            # allowance that is not currently being counted.
            return Decision(True, rule.capacity, rule.capacity, 0)
        return decision

    async def close(self) -> None:
        await self._backend.close()


def bucket_key(rule: str, identity: str) -> str:
    """The Redis key for one bucket.

    The identity is hashed. It can be an email address or a client IP, and
    neither belongs in a key that shows up in a keyspace listing, a slow-log
    entry or a memory dump - the limiter only ever needs to know that two
    requests came from the same place, not where that was.
    """
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return f"rl:{KEY_VERSION}:{rule}:{digest}"


def rules_from_settings(settings: Settings) -> dict[str, Rule]:
    return {
        READ: Rule(READ, settings.rate_limit_read_burst, settings.rate_limit_read_per_minute / 60),
        WRITE: Rule(
            WRITE, settings.rate_limit_write_burst, settings.rate_limit_write_per_minute / 60
        ),
        AUTH: Rule(AUTH, settings.rate_limit_auth_burst, settings.rate_limit_auth_per_minute / 60),
    }


def build_rate_limiter(settings: Settings) -> RateLimiter:
    """The limiter this process should use.

    Redis everywhere except tests, exactly as the queue, the cache and the
    event bus are chosen - and for the sharper version of the same reason: a
    per-replica limit is not the limit anybody configured. The in-memory
    backend under ``APP_ENV=test`` keeps the suite hermetic and exercises the
    identical bucket arithmetic.
    """
    backend: RateLimitBackend
    if settings.app_env == "test":
        backend = InMemoryRateLimitBackend()
    else:
        from app.workers.queue import build_redis

        backend = RedisRateLimitBackend(build_redis(settings.redis_url))

    rules = rules_from_settings(settings)
    if not settings.rate_limit_enabled:
        logger.warning("rate limiting is disabled")
    else:
        logger.info(
            "rate limiting configured",
            extra={
                "backend": type(backend).__name__,
                "rules": {name: rule.per_minute for name, rule in rules.items()},
            },
        )
    return RateLimiter(backend, rules, enabled=settings.rate_limit_enabled)


def refuse(decision: Decision, *, rule: str) -> RateLimited:
    """The error a refused request raises, carrying its own headers."""
    return RateLimited(
        headers={
            "Retry-After": str(decision.retry_after_seconds),
            "X-RateLimit-Limit": str(decision.limit),
            "X-RateLimit-Remaining": "0",
        },
        context={"rule": rule, "retry_after_seconds": decision.retry_after_seconds},
    )
