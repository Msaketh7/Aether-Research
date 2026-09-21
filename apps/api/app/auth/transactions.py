"""The half-finished sign-in, held while the person is at the provider.

Between the redirect out and the callback back there is state this system must
remember and must not hand to the browser: the PKCE verifier, the nonce, the
`state` it generated, and where the person was going. The browser carries only
an opaque handle in a short-lived `HttpOnly` cookie; everything the handle
names stays here.

**Two independent bindings, and both are checked.** The cookie proves the
callback reached the same browser the flow started in. The `state` proves the
provider's response belongs to that flow. Checking only the cookie lets an
attacker paste their own provider response into a victim's browser; checking
only `state` lets a flow started in one browser be finished in another. Login
CSRF needs just one of those to be missing.

**A transaction is single-use, and the read is what enforces it.** `take` is a
get-and-delete, atomic where the backing store can be, so a `state` that has
been consumed cannot be consumed again. If this were a get followed by a
delete, two callbacks arriving together would both succeed - which is exactly
the race an attacker replays a captured callback into.

**Fail-closed here is correct**, unlike the revocation index. A lost
transaction means one sign-in attempt fails and the person presses the button
again; there is no degraded mode worth preserving and nothing is signed in as
a result.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import secrets
from dataclasses import asdict, dataclass
from typing import Protocol

from app.auth.providers.base import Connection, ProviderName
from app.core.logging import get_logger

logger = get_logger(__name__)

KEY_PREFIX = "aether:oauth-txn:v1"

#: Entropy in the handle that names a transaction. It is a bearer credential
#: for one sign-in attempt, so it gets the same 256 bits everything else does.
HANDLE_BYTES = 32

#: The cookie the handle travels in. Separate from the session cookie, cleared
#: as soon as the callback resolves, and scoped tightly enough that it is not
#: sent with ordinary API traffic.
TRANSACTION_COOKIE_NAME = "aether_oauth"


def mint_handle() -> str:
    return secrets.token_urlsafe(HANDLE_BYTES)


@dataclass(frozen=True, slots=True)
class PendingAuthorization:
    """Everything the callback needs, and nothing the browser may see."""

    provider: ProviderName
    connection: Connection
    state: str
    nonce: str
    code_verifier: str
    #: Repeated in the token request, so it must be byte-identical to the one
    #: sent in the authorization request - hence stored rather than rebuilt.
    redirect_uri: str
    #: Where to land afterwards. Already validated as a path on this app's
    #: origin when the flow started; stored so the callback does not have to
    #: trust a parameter of its own.
    destination: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> PendingAuthorization | None:
        try:
            data = json.loads(raw)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        try:
            return cls(**data)
        except TypeError:
            # Written by a different build of this class. Treated as absent:
            # the sign-in fails and is retried under the current shape.
            return None


class TransactionStore(Protocol):
    async def put(self, handle: str, txn: PendingAuthorization, *, ttl_seconds: int) -> None: ...

    async def take(self, handle: str) -> PendingAuthorization | None:
        """Read and remove in one step. Never a get followed by a delete."""
        ...


class InMemoryTransactionStore:
    """For `APP_ENV=test` and single-process development."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[PendingAuthorization, dt.datetime]] = {}
        self._lock = asyncio.Lock()

    async def put(self, handle: str, txn: PendingAuthorization, *, ttl_seconds: int) -> None:
        now = dt.datetime.now(dt.UTC)
        async with self._lock:
            self._entries = {k: v for k, v in self._entries.items() if v[1] > now}
            self._entries[handle] = (txn, now + dt.timedelta(seconds=ttl_seconds))

    async def take(self, handle: str) -> PendingAuthorization | None:
        async with self._lock:
            entry = self._entries.pop(handle, None)
        if entry is None:
            return None
        txn, expires_at = entry
        # Popped either way: an expired transaction is consumed rather than
        # left behind, so a late callback cannot find it on a second attempt.
        return txn if expires_at > dt.datetime.now(dt.UTC) else None


class RedisTransactionStore:
    """The deployment's store.

    Uses `GETDEL`, which is why the single-use property holds across processes:
    two API instances handling a replayed callback cannot both win.
    """

    def __init__(self, redis: object) -> None:
        self._redis = redis

    def _key(self, handle: str) -> str:
        return f"{KEY_PREFIX}:{handle}"

    async def put(self, handle: str, txn: PendingAuthorization, *, ttl_seconds: int) -> None:
        await self._redis.set(  # type: ignore[attr-defined]
            self._key(handle), txn.to_json(), ex=max(1, ttl_seconds)
        )

    async def take(self, handle: str) -> PendingAuthorization | None:
        # Imported here rather than at module scope, like every other
        # third-party import in this codebase: only a deployment reaches this
        # class, and `import app.main` should not pull the Redis package in on
        # behalf of a store the test suite never constructs.
        from redis.exceptions import ResponseError

        key = self._key(handle)
        try:
            raw = await self._redis.getdel(key)  # type: ignore[attr-defined]
        except ResponseError:
            # `GETDEL` needs Redis 6.2; an older server answers "unknown
            # command". Raised rather than worked around, because the
            # alternatives are not equivalent: a GET followed by a DEL lets two
            # callers both read before either deletes, which is precisely the
            # replay this store's single-use property exists to prevent. The
            # client library always has the method, so this is a server
            # version, not a missing attribute.
            raise RuntimeError(
                "The OAuth transaction store needs Redis 6.2 or newer for GETDEL."
            ) from None
        except Exception:
            # Unreachable store: fail closed. One sign-in attempt fails and the
            # person tries again; nothing is signed in as a result.
            logger.error("could not read the OAuth transaction store")
            return None

        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return PendingAuthorization.from_json(raw)
