"""Persistence boundary for research runs.

The protocol lives here, next to the domain it serves; the Postgres
implementation lives in ``app/db/repositories``. The service layer only ever
sees this interface, which is what let Phase 3 replace the storage engine
without touching a single endpoint.

Every read is scoped by ``user_id``. Ownership is not an optional argument that
a caller might forget - it is part of every signature.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
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

    async def commit(self) -> None:
        """Make everything written so far durable, before the request ends.

        Normally the request boundary handles this. One case needs it earlier:
        a run must be committed *before* its job is dispatched, so that a queue
        failure leaves a recoverable `queued` row instead of rolling the user's
        request away (ADR 0005). That is a domain requirement, not a
        persistence detail, which is why it is on the interface.
        """
        ...


class UploadAttachments(Protocol):
    """What run creation needs from the uploads store (``document_ids``)."""

    async def owned_ids(self, user_id: UUID, upload_ids: Sequence[UUID]) -> set[UUID]:
        """The subset of ``upload_ids`` that are this user's uploads."""
        ...

    async def attach(self, run_id: UUID, upload_ids: Sequence[UUID]) -> None:
        """Record the attachment, in the same transaction as the run."""
        ...


def utcnow() -> dt.datetime:
    """Timezone-aware now. Naive datetimes are a bug waiting for a timezone."""
    return dt.datetime.now(dt.UTC)
