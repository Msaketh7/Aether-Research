"""Persistence boundary for research runs.

The interface is the point. Phase 2 ships an in-memory adapter so the API
surface can be built and tested end to end; Phase 3 adds the SQLAlchemy adapter
against Postgres. Nothing above this file changes when that happens, because
the service layer only ever sees :class:`ResearchRepository`.

Every read is scoped by ``user_id``. Ownership is not an optional argument that
a caller might forget - it is part of every signature.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from app.core.enums import RunStatus
from app.research.schemas import ResearchRun


class ResearchRepository(Protocol):
    """What the research service needs from storage."""

    async def add(self, run: ResearchRun) -> ResearchRun: ...

    async def get(self, run_id: UUID, *, user_id: UUID) -> ResearchRun | None:
        """The run, or ``None`` if it does not exist **or** is not this user's.

        Collapsing "missing" and "not yours" is deliberate: a 403 on someone
        else's id confirms the id exists.
        """
        ...

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        limit: int,
        after_id: UUID | None = None,
        status: RunStatus | None = None,
        query: str | None = None,
    ) -> tuple[list[ResearchRun], bool]:
        """One bounded page, plus whether more rows follow."""
        ...

    async def all_for_user(self, user_id: UUID) -> list[ResearchRun]:
        """Every run for one user. Used only for aggregate statistics."""
        ...

    async def count_active(self, user_id: UUID) -> int:
        """Runs that have not reached a terminal status."""
        ...

    async def update(self, run: ResearchRun) -> ResearchRun: ...


class InMemoryResearchRepository:
    """Process-local storage.

    Replaced in Phase 3. Until then this is the honest state of the system: a
    restart loses runs, and the API README says so rather than implying
    durability the service does not have.
    """

    def __init__(self) -> None:
        self._runs: dict[UUID, ResearchRun] = {}
        self._lock = asyncio.Lock()

    async def add(self, run: ResearchRun) -> ResearchRun:
        async with self._lock:
            self._runs[run.id] = run
        return run

    async def get(self, run_id: UUID, *, user_id: UUID) -> ResearchRun | None:
        async with self._lock:
            run = self._runs.get(run_id)
        if run is None or run.user_id != user_id:
            return None
        return run

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        limit: int,
        after_id: UUID | None = None,
        status: RunStatus | None = None,
        query: str | None = None,
    ) -> tuple[list[ResearchRun], bool]:
        async with self._lock:
            runs = [run for run in self._runs.values() if run.user_id == user_id]

        if status is not None:
            runs = [run for run in runs if run.status is status]
        if query:
            needle = query.lower()
            runs = [
                run for run in runs if needle in run.title.lower() or needle in run.question.lower()
            ]

        # Newest first, with the id as a tiebreak so the order is total and a
        # cursor cannot skip or repeat a row when timestamps collide.
        runs.sort(key=lambda run: (run.created_at, str(run.id)), reverse=True)

        if after_id is not None:
            index = next((i for i, run in enumerate(runs) if run.id == after_id), None)
            runs = runs[index + 1 :] if index is not None else []

        # Fetch one extra row to learn whether another page exists without a
        # second count query.
        window = runs[: limit + 1]
        return window[:limit], len(window) > limit

    async def all_for_user(self, user_id: UUID) -> list[ResearchRun]:
        async with self._lock:
            return [run for run in self._runs.values() if run.user_id == user_id]

    async def count_active(self, user_id: UUID) -> int:
        async with self._lock:
            return sum(
                1
                for run in self._runs.values()
                if run.user_id == user_id and not run.status.is_terminal
            )

    async def update(self, run: ResearchRun) -> ResearchRun:
        async with self._lock:
            self._runs[run.id] = run
        return run

    async def clear(self) -> None:
        async with self._lock:
            self._runs.clear()


def utcnow() -> datetime:
    """Timezone-aware now. Naive datetimes are a bug waiting for a timezone."""
    return datetime.now(UTC)
