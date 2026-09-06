"""User rows.

Registration and password handling are FR-1 and land in Phase 20. What exists
now is the one operation the rest of the system cannot work without: making
sure the acting principal has a row, because every research run has a foreign
key to `users` and an orphaned run is not representable.
"""

from __future__ import annotations

import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import UserRow


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure(self, user_id: uuid.UUID, email: str, *, name: str = "") -> None:
        """Insert the user if absent, leave them untouched if present.

        ``ON CONFLICT DO NOTHING`` rather than select-then-insert: two
        concurrent first requests from the same principal would otherwise race
        and one would fail on the unique index.
        """
        statement = (
            insert(UserRow)
            .values(id=user_id, email=email, name=name or email.split("@")[0])
            .on_conflict_do_nothing(index_elements=[UserRow.id])
        )
        await self._session.execute(statement)

    async def get(self, user_id: uuid.UUID) -> UserRow | None:
        return await self._session.get(UserRow, user_id)
