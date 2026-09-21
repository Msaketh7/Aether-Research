"""The signing keys a provider publishes, cached.

Verifying a provider's token means holding its public keys, and those keys
rotate. Fetching them per request would put an outbound HTTP call on the hot
path of every authenticated request and make this system's availability a
function of the provider's; never refetching would mean a rotation signs
everybody out until a redeploy. This is the middle: cache with a TTL, and
refetch out of band when a token arrives signed by a key that is not held.

**The refetch is what needs care.** "Unknown `kid` means refetch" is an
attacker-controlled trigger - a token is unauthenticated input, and its header
is chosen by whoever sends it. Left alone it is an amplifier: a few hundred
requests a second carrying random `kid`s become a few hundred requests a
second at the provider's JWKS endpoint, from our address, which gets this
system rate-limited or blocked and takes sign-in down for everyone. So a
refetch is allowed at most once per cooldown per provider, and a token whose
key is still unknown after that is simply refused.

**No SSRF guard here, deliberately.** The guard in `app/sources` exists because
those URLs come from search results and model output. A JWKS URL comes from
this deployment's own settings, is validated as HTTPS at construction, and is
never influenced by a request. Running it through the guard would suggest the
input is untrusted and hide where the real boundary is.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from typing import Any

from joserfc.jwk import KeySet

from app.auth.providers.base import ProviderError
from app.core.logging import get_logger

logger = get_logger(__name__)

#: How long a fetched key set is served before it is refreshed.
DEFAULT_TTL_SECONDS = 600

#: The shortest gap between two fetches prompted by an unknown `kid`. See the
#: module docstring: this is the amplification control, not a performance one.
DEFAULT_REFRESH_COOLDOWN_SECONDS = 60

#: A key set is a handful of keys. Anything larger is not one, and reading it
#: would be letting an upstream decide how much memory this process uses.
MAX_JWKS_BYTES = 256 * 1024

DEFAULT_TIMEOUT_SECONDS = 5.0


class JwksCache:
    """One provider's published keys.

    Not shared between providers: each has its own URL, its own TTL clock and
    its own cooldown, and one provider being slow must not stall another's
    verification.
    """

    def __init__(
        self,
        url: str,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        cooldown_seconds: int = DEFAULT_REFRESH_COOLDOWN_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: Any | None = None,
    ) -> None:
        if not url.startswith("https://"):
            # Plain HTTP here would mean anybody on the path can substitute the
            # keys that decide who is signed in. Refused at construction so it
            # is a startup failure rather than a silent downgrade.
            raise ValueError(f"A JWKS URL must be HTTPS, got {url!r}")

        self._url = url
        self._ttl = dt.timedelta(seconds=ttl_seconds)
        self._cooldown = dt.timedelta(seconds=cooldown_seconds)
        self._timeout = timeout_seconds
        # Injected so the tests can drive a scripted provider through the
        # real client, the way the model adapters are tested. `None` in a
        # deployment, which is an ordinary network client.
        self._transport = transport

        self._keys: KeySet | None = None
        self._fetched_at: dt.datetime | None = None
        self._last_attempt_at: dt.datetime | None = None
        # One fetch at a time. Without this, a cold cache under concurrent
        # sign-ins sends one request per caller to the provider.
        self._lock = asyncio.Lock()

    async def key_set(self, *, kid: str | None = None) -> KeySet:
        """The provider's keys, refreshing when stale or when `kid` is unknown."""
        now = dt.datetime.now(dt.UTC)

        if self._keys is not None and not self._is_stale(now):
            if kid is None or self._holds(kid):
                return self._keys
            # Held, fresh, and does not contain the key this token names. Either
            # a rotation happened inside the TTL, or the token is not ours.
            if not self._may_refetch(now):
                raise ProviderError(
                    "That sign-in could not be verified.",
                    code="unknown_signing_key",
                )

        async with self._lock:
            # Re-checked inside the lock: whoever was holding it may have just
            # done the fetch this caller was about to make.
            now = dt.datetime.now(dt.UTC)
            held = self._keys
            fresh = held is not None and not self._is_stale(now)
            if held is not None and fresh and (kid is None or self._holds(kid)):
                return held
            return await self._fetch(now)

    def _is_stale(self, now: dt.datetime) -> bool:
        return self._fetched_at is None or now - self._fetched_at >= self._ttl

    def _holds(self, kid: str) -> bool:
        if self._keys is None:
            return False
        return any(key.kid == kid for key in self._keys.keys)

    def _may_refetch(self, now: dt.datetime) -> bool:
        return self._last_attempt_at is None or now - self._last_attempt_at >= self._cooldown

    async def _fetch(self, now: dt.datetime) -> KeySet:
        # Recorded before the request, not after: a provider that times out
        # must still spend the cooldown, or a failing upstream becomes a
        # retry storm aimed at itself.
        self._last_attempt_at = now

        # Imported here rather than at module scope, like every other heavy
        # third-party import in this codebase.
        import httpx2

        try:
            async with httpx2.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                response = await client.get(self._url, headers={"Accept": "application/json"})
                response.raise_for_status()
                if len(response.content) > MAX_JWKS_BYTES:
                    raise ProviderError(
                        "That sign-in provider returned an unusable key set.",
                        code="jwks_too_large",
                    )
                payload: Any = response.json()
        except ProviderError:
            raise
        except Exception as exc:
            # Serve stale keys rather than failing, when there are any. A
            # provider being briefly unreachable should not sign out everybody
            # holding a token its previous keys can still verify; the keys are
            # public and a slightly old copy is not a weaker check.
            if self._keys is not None:
                logger.warning(
                    "could not refresh signing keys; serving the cached set",
                    extra={"jwks_url": self._url, "reason": type(exc).__name__},
                )
                return self._keys
            raise ProviderError(
                "That sign-in provider is unavailable.",
                code="jwks_unavailable",
            ) from exc

        try:
            keys = KeySet.import_key_set(payload)
        except Exception as exc:
            raise ProviderError(
                "That sign-in provider returned an unusable key set.",
                code="jwks_malformed",
            ) from exc

        self._keys = keys
        self._fetched_at = now
        logger.info("signing keys refreshed", extra={"key_count": len(keys.keys)})
        return keys
