"""One in-flight call per key, however many callers ask for it.

The phase requires that simultaneous identical fetches and searches are done
once. A cache alone does not do that: two researchers that ask for the same URL
in the same millisecond both miss, both fetch, and both store - which is the
exact case a fan-out of parallel researchers produces, because they were given
overlapping subtasks by the same planner.

**The scope is one process, and that is the scope that matters.** The callers
being deduplicated are the researchers of one run, which run inside one
worker's graph. A cross-process barrier would need a distributed lock, a lease,
and an answer for the holder that dies while others wait - real machinery, for
a case the architecture does not produce: two workers never execute the same
run (ADR 0017), and two different runs wanting one URL in the same instant is a
coincidence rather than a pattern. The shared Redis cache serves the second of
those one round trip later, which is what it is for.

A failed call is shared like a successful one. The waiters would each have made
the same request against the same unavailable host and received the same error;
giving them the leader's is the same outcome, sooner. A *cancelled* leader also
cancels its waiters, which is correct in the only situation that produces one:
the process is shutting down and the whole run is being torn down with it.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast


class SingleFlight:
    """Coalesces concurrent calls that share a key."""

    def __init__(self) -> None:
        self._inflight: dict[str, asyncio.Future[Any]] = {}

    def is_inflight(self, key: str) -> bool:
        return key in self._inflight

    async def run[T](self, key: str, operation: Callable[[], Awaitable[T]]) -> tuple[T, bool]:
        """Run ``operation``, or wait for the one already running for ``key``.

        Returns the value and whether this caller was the one that ran it, so a
        cost nobody paid twice is not recorded twice either.
        """
        existing = self._inflight.get(key)
        if existing is not None:
            # Shielded: a waiter that gives up must not cancel the call the
            # leader and every other waiter are relying on.
            return cast("T", await asyncio.shield(existing)), False

        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            value = await operation()
        except BaseException as exc:
            if not future.done():
                future.set_exception(exc)
            raise
        else:
            if not future.done():
                future.set_result(value)
            return value, True
        finally:
            self._inflight.pop(key, None)
            _discard(future)


def _discard(future: asyncio.Future[Any]) -> None:
    """Retrieve a failed future's exception so asyncio does not log it again.

    The leader has already raised it to its own caller. Without this, a future
    nobody waited on is reported at collection time as an exception that was
    never retrieved - a warning about an error that was in fact handled.
    """
    if future.done() and not future.cancelled() and future.exception() is not None:
        return
