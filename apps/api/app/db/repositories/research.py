"""Postgres-backed research run storage.

Implements the ``ResearchRepository`` protocol the service layer already talks
to, so nothing above this file changed when the in-memory adapter was replaced.

Two rules hold for every query here:

* **bounded** - each one carries a LIMIT or addresses a single row by primary
  key. There is no query whose cost grows with a user's history;
* **scoped** - ``user_id`` is in the WHERE clause of every read, not applied
  afterwards in Python. Authorisation that filters after fetching is a
  authorisation bug waiting for a paginated endpoint to expose it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import CursorResult, Select, and_, delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import ResearchMode, RunStatus
from app.db.models.report import ReportRow
from app.db.models.research import ResearchRunRow
from app.research.schemas import ResearchRun, RunError, RunLimits, RunUsage

#: Guard on every list query, even when a caller forgets to pass one.
ABSOLUTE_MAX_ROWS = 200


def _to_float(value: Decimal | float | None) -> float:
    """Numeric columns come back as Decimal; the DTOs are float."""
    return float(value) if value is not None else 0.0


def _elapsed_seconds(row: ResearchRunRow, now: dt.datetime) -> int:
    """Wall-clock seconds so far, or the final duration once finished."""
    if row.started_at is None:
        return 0
    end = row.completed_at or now
    return max(0, int((end - row.started_at).total_seconds()))


def to_run_dto(row: ResearchRunRow, *, has_report: bool = False) -> ResearchRun:
    """One row as the DTO every layer above the database speaks.

    A module-level function rather than a method: the worker writes the same
    table through its own repository (``app.db.repositories.worker``) and has to
    hand back the same object, and two mappings of one table would drift.
    """
    now = dt.datetime.now(dt.UTC)
    limits = row.limits or {}
    return ResearchRun(
        id=row.id,
        user_id=row.user_id,
        parent_run_id=row.parent_run_id,
        title=row.title,
        question=row.question,
        mode=ResearchMode(row.mode),
        depth=row.depth,
        domains=list(row.domains or []),
        date_range_start=row.date_range_start,
        date_range_end=row.date_range_end,
        status=RunStatus(row.status),
        progress=_to_float(row.progress),
        limits=RunLimits.model_validate(limits),
        usage=RunUsage(
            iterations=row.iteration_count,
            sources=row.source_count,
            elapsed_seconds=_elapsed_seconds(row, now),
            total_tokens=row.total_tokens,
            cost_usd=_to_float(row.total_cost_usd),
        ),
        source_count=row.source_count,
        claim_count=row.claim_count,
        contradiction_count=row.contradiction_count,
        coverage_caveat=row.coverage_caveat,
        has_report=has_report,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        error=RunError.model_validate(row.error) if row.error else None,
    )


class SqlAlchemyResearchRepository:
    """Reads and writes ``research_runs`` for one request's session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- mapping ----------------------------------------------------------

    def _to_dto(self, row: ResearchRunRow, *, has_report: bool = False) -> ResearchRun:
        return to_run_dto(row, has_report=has_report)

    @staticmethod
    def _to_row_values(run: ResearchRun) -> dict[str, Any]:
        return {
            "id": run.id,
            "user_id": run.user_id,
            "parent_run_id": run.parent_run_id,
            "title": run.title,
            "question": run.question,
            "mode": run.mode.value,
            "depth": run.depth,
            "domains": list(run.domains),
            "date_range_start": run.date_range_start,
            "date_range_end": run.date_range_end,
            "status": run.status.value,
            "progress": run.progress,
            "iteration_count": run.usage.iterations,
            "total_cost_usd": run.usage.cost_usd,
            "total_tokens": run.usage.total_tokens,
            "source_count": run.source_count,
            "claim_count": run.claim_count,
            "contradiction_count": run.contradiction_count,
            "coverage_caveat": run.coverage_caveat,
            "limits": run.limits.model_dump(),
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "error": run.error.model_dump() if run.error else None,
        }

    # --- commands ---------------------------------------------------------

    async def add(self, run: ResearchRun) -> ResearchRun:
        row = ResearchRunRow(**self._to_row_values(run))
        # created_at is a server default, so the row must be flushed before the
        # DTO can carry a real timestamp rather than a guess.
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return self._to_dto(row)

    async def update(self, run: ResearchRun) -> ResearchRun:
        row = await self._session.get(ResearchRunRow, run.id)
        if row is None:
            # The caller holds a DTO for a row that no longer exists; adding it
            # back would resurrect deleted data.
            raise LookupError(f"research run {run.id} no longer exists")

        for key, value in self._to_row_values(run).items():
            if key != "id":
                setattr(row, key, value)
        await self._session.flush()
        await self._session.refresh(row)
        return self._to_dto(row, has_report=await self._has_report(row.id))

    async def commit(self) -> None:
        await self._session.commit()

    # --- queries ----------------------------------------------------------

    async def get(self, run_id: uuid.UUID, *, user_id: uuid.UUID) -> ResearchRun | None:
        statement = select(ResearchRunRow).where(
            ResearchRunRow.id == run_id,
            # Ownership in the WHERE clause: a run that is not this user's is
            # simply not selected, so it cannot be leaked by a later mistake.
            ResearchRunRow.user_id == user_id,
        )
        row = (await self._session.execute(statement)).scalar_one_or_none()
        if row is None:
            return None
        return self._to_dto(row, has_report=await self._has_report(row.id))

    async def status_of(self, run_id: uuid.UUID, *, user_id: uuid.UUID) -> RunStatus | None:
        """One column by primary key. A running graph reads it at every node boundary."""
        statement = select(ResearchRunRow.status).where(
            ResearchRunRow.id == run_id,
            ResearchRunRow.user_id == user_id,
        )
        value = (await self._session.execute(statement)).scalar_one_or_none()
        return RunStatus(value) if value is not None else None

    def _base_query(
        self,
        user_id: uuid.UUID,
        *,
        status: RunStatus | None,
        query: str | None,
    ) -> Select[tuple[ResearchRunRow]]:
        statement = select(ResearchRunRow).where(ResearchRunRow.user_id == user_id)
        if status is not None:
            statement = statement.where(ResearchRunRow.status == status.value)
        if query:
            # ILIKE with a leading wildcard cannot use a b-tree index. Fine at
            # this scale; a trigram index is the documented next step if the
            # search latency ever shows up in the p95.
            pattern = f"%{query}%"
            statement = statement.where(
                or_(
                    ResearchRunRow.title.ilike(pattern),
                    ResearchRunRow.question.ilike(pattern),
                )
            )
        return statement

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int,
        after_id: uuid.UUID | None = None,
        status: RunStatus | None = None,
        query: str | None = None,
    ) -> tuple[list[ResearchRun], bool]:
        bounded = max(1, min(limit, ABSOLUTE_MAX_ROWS))
        statement = self._base_query(user_id, status=status, query=query)

        if after_id is not None:
            # Keyset pagination on (created_at DESC, id DESC), matching the
            # index. An OFFSET would degrade linearly and can skip or repeat a
            # row when a new run is created mid-traversal.
            anchor = (
                await self._session.execute(
                    select(ResearchRunRow.created_at, ResearchRunRow.id).where(
                        ResearchRunRow.id == after_id,
                        ResearchRunRow.user_id == user_id,
                    )
                )
            ).one_or_none()
            if anchor is None:
                # The cursor points at a run that was deleted or never belonged
                # to this user. An empty page is safer than silently restarting
                # from the top, which would loop a paginating client forever.
                return [], False
            created_at, anchor_id = anchor
            statement = statement.where(
                or_(
                    ResearchRunRow.created_at < created_at,
                    and_(
                        ResearchRunRow.created_at == created_at,
                        ResearchRunRow.id < anchor_id,
                    ),
                )
            )

        statement = statement.order_by(
            ResearchRunRow.created_at.desc(), ResearchRunRow.id.desc()
        ).limit(bounded + 1)  # one extra row answers "is there another page?"

        rows = list((await self._session.execute(statement)).scalars())
        has_more = len(rows) > bounded
        return [self._to_dto(row) for row in rows[:bounded]], has_more

    async def all_for_user(self, user_id: uuid.UUID) -> list[ResearchRun]:
        """Bounded even though the name suggests otherwise.

        Only the dashboard aggregate uses this. The cap keeps one user with a
        very long history from turning a dashboard load into a table scan; the
        statistics it feeds are indicative, and the cap is documented in the
        response rather than hidden.
        """
        statement = (
            select(ResearchRunRow)
            .where(ResearchRunRow.user_id == user_id)
            .order_by(ResearchRunRow.created_at.desc())
            .limit(ABSOLUTE_MAX_ROWS)
        )
        rows = (await self._session.execute(statement)).scalars()
        return [self._to_dto(row) for row in rows]

    async def count_active(self, user_id: uuid.UUID) -> int:
        terminal = [status.value for status in RunStatus if status.is_terminal]
        statement = (
            select(func.count())
            .select_from(ResearchRunRow)
            .where(
                ResearchRunRow.user_id == user_id,
                ResearchRunRow.status.not_in(terminal),
            )
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def _has_report(self, run_id: uuid.UUID) -> bool:
        statement = select(exists().where(ReportRow.run_id == run_id))
        return bool((await self._session.execute(statement)).scalar())

    # --- maintenance ------------------------------------------------------

    async def delete_for_user(self, user_id: uuid.UUID) -> int:
        """Delete every run for a user. Used by tests and by account deletion.

        Cascades handle the dependent rows; doing it in one statement avoids
        loading a history into memory to throw it away.
        """
        result: CursorResult[Any] = await self._session.execute(  # type: ignore[assignment]
            delete(ResearchRunRow).where(ResearchRunRow.user_id == user_id)
        )
        return int(result.rowcount or 0)
