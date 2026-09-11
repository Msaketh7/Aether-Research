"""Postgres storage for uploads and their attachment to runs.

Bounded and scoped like every repository here: each read has ``user_id`` in its
WHERE clause, and each list has a LIMIT.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.upload import ResearchRunUploadRow, UploadRow

#: Guard on every list query, even when a caller forgets to pass one.
ABSOLUTE_MAX_ROWS = 200


class SqlAlchemyUploadRepository:
    """Reads and writes ``uploads`` and ``research_run_uploads`` for one session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # --- commands ---------------------------------------------------------

    async def add(
        self,
        *,
        user_id: uuid.UUID,
        filename: str,
        fmt: str,
        mime_type: str,
        size_bytes: int,
        content_hash: str,
        storage_key: str,
        charset: str | None,
    ) -> tuple[UploadRow, bool]:
        """Insert, or return the existing row for the same bytes. ``True`` if new.

        ``ON CONFLICT DO NOTHING`` rather than check-then-insert: two concurrent
        uploads of one file by one user - a double-clicked button - must produce
        one row, and only the database can settle that race.
        """
        statement = (
            pg_insert(UploadRow)
            .values(
                user_id=user_id,
                filename=filename,
                format=fmt,
                mime_type=mime_type,
                size_bytes=size_bytes,
                content_hash=content_hash,
                storage_key=storage_key,
                charset=charset,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "content_hash"])
            .returning(UploadRow)
        )
        created = (await self._session.execute(statement)).scalar_one_or_none()
        if created is not None:
            return created, True

        existing = await self.get_by_hash(user_id, content_hash)
        if existing is None:  # pragma: no cover - a conflict implies a committed row
            raise LookupError("an upload conflicted with a row that is not there")
        return existing, False

    async def attach(self, run_id: uuid.UUID, upload_ids: Sequence[uuid.UUID]) -> None:
        """Record which uploads a run was created with. Idempotent."""
        if not upload_ids:
            return
        await self._session.execute(
            pg_insert(ResearchRunUploadRow)
            .values([{"run_id": run_id, "upload_id": upload_id} for upload_id in upload_ids])
            .on_conflict_do_nothing()
        )

    # --- queries ----------------------------------------------------------

    async def get(self, upload_id: uuid.UUID, *, user_id: uuid.UUID) -> UploadRow | None:
        """The upload, or ``None`` if it does not exist **or** is not this user's."""
        statement = select(UploadRow).where(UploadRow.id == upload_id, UploadRow.user_id == user_id)
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_by_hash(self, user_id: uuid.UUID, content_hash: str) -> UploadRow | None:
        statement = select(UploadRow).where(
            UploadRow.user_id == user_id, UploadRow.content_hash == content_hash
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        *,
        limit: int,
        after_id: uuid.UUID | None = None,
    ) -> tuple[list[UploadRow], bool]:
        """One page, newest first, plus whether another follows.

        Keyset pagination on (created_at DESC, id DESC), which the index serves,
        for the same reasons as the run history (see the research repository).
        """
        bounded = max(1, min(limit, ABSOLUTE_MAX_ROWS))
        statement = select(UploadRow).where(UploadRow.user_id == user_id)

        if after_id is not None:
            anchor = (
                await self._session.execute(
                    select(UploadRow.created_at, UploadRow.id).where(
                        UploadRow.id == after_id, UploadRow.user_id == user_id
                    )
                )
            ).one_or_none()
            if anchor is None:
                return [], False
            created_at, anchor_id = anchor
            statement = statement.where(
                or_(
                    UploadRow.created_at < created_at,
                    and_(UploadRow.created_at == created_at, UploadRow.id < anchor_id),
                )
            )

        statement = statement.order_by(UploadRow.created_at.desc(), UploadRow.id.desc()).limit(
            bounded + 1
        )
        rows = list((await self._session.execute(statement)).scalars())
        return rows[:bounded], len(rows) > bounded

    async def owned_ids(
        self, user_id: uuid.UUID, upload_ids: Sequence[uuid.UUID]
    ) -> set[uuid.UUID]:
        """Which of ``upload_ids`` belong to this user.

        Returns the subset rather than a yes/no, so the caller can say *which*
        ids were not found - without ever saying whether an id exists for
        someone else.
        """
        if not upload_ids:
            return set()
        statement = (
            select(UploadRow.id)
            .where(UploadRow.user_id == user_id, UploadRow.id.in_(list(upload_ids)))
            .limit(ABSOLUTE_MAX_ROWS)
        )
        return set((await self._session.execute(statement)).scalars())

    async def attached_to_run(self, run_id: uuid.UUID, *, user_id: uuid.UUID) -> list[UploadRow]:
        """The uploads a run was created with, in the order they were attached.

        Scoped by the upload's owner as well as the run: attachment already
        checked ownership, and checking again here costs nothing and means a
        row written by any other route still cannot pull in someone else's file.
        """
        statement = (
            select(UploadRow)
            .join(ResearchRunUploadRow, ResearchRunUploadRow.upload_id == UploadRow.id)
            .where(ResearchRunUploadRow.run_id == run_id, UploadRow.user_id == user_id)
            .order_by(ResearchRunUploadRow.created_at, UploadRow.id)
            .limit(ABSOLUTE_MAX_ROWS)
        )
        return list((await self._session.execute(statement)).scalars())
