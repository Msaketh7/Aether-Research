"""User rows.

Everything the accounts system reads or writes about a person, and nothing
else. The email column is ``citext``, so a lookup by address is
case-insensitive in the database rather than by a ``lower()`` every caller has
to remember - and two people cannot register the same address in different
cases.

``password_hash`` is nullable, and a null one means *this account has no
password*, not *this account has an empty password*. Every path that verifies a
credential treats null as a refusal; there is no code path where a missing hash
authenticates anybody.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import UserRow


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ensure(self, user_id: uuid.UUID, email: str, *, name: str = "") -> None:
        """Insert the user if absent, leave them untouched if present.

        The development principal's row. ``ON CONFLICT DO NOTHING`` rather than
        select-then-insert: two concurrent first requests from the same
        principal would otherwise race and one would fail on the unique index.
        """
        statement = (
            insert(UserRow)
            .values(id=user_id, email=email, name=name or email.split("@")[0])
            .on_conflict_do_nothing(index_elements=[UserRow.id])
        )
        await self._session.execute(statement)

    async def get(self, user_id: uuid.UUID) -> UserRow | None:
        return await self._session.get(UserRow, user_id)

    async def get_by_email(self, email: str) -> UserRow | None:
        """The account for an address, matched case-insensitively by ``citext``."""
        statement = select(UserRow).where(UserRow.email == email)
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def create(
        self, *, email: str, password_hash: str, name: str, role: str = "user"
    ) -> UserRow | None:
        """Register an account, or report that the address is taken.

        Returns ``None`` on a duplicate address rather than raising, because to
        this layer a taken address is an ordinary outcome; what the *endpoint*
        says about it is a separate decision, made where account enumeration is
        the consideration (see ``app/api/v1/auth.py``).

        A SAVEPOINT around the insert: a unique-violation poisons the enclosing
        transaction, and the request still has a rate-limit decision and an
        audit row to commit afterwards.
        """
        row = UserRow(email=email, password_hash=password_hash, name=name, role=role)
        try:
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            return None
        await self._session.refresh(row)
        return row

    async def set_password_hash(self, user_id: uuid.UUID, password_hash: str) -> None:
        """Replace the stored hash, leaving everything else alone.

        Used to re-hash in place when a login succeeds against parameters
        weaker than the ones now configured.
        """
        await self._session.execute(
            update(UserRow).where(UserRow.id == user_id).values(password_hash=password_hash)
        )

    async def record_login(self, user_id: uuid.UUID, *, at: dt.datetime) -> None:
        await self._session.execute(
            update(UserRow).where(UserRow.id == user_id).values(last_login_at=at)
        )

    async def replace_settings(
        self, user_id: uuid.UUID, settings: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Overwrite the preferences blob, returning what is now stored.

        A whole-document write rather than a merge: the caller has already
        validated a complete preferences object, and a partial write would let
        an unknown key survive validation by never being present at write time.
        """
        statement = (
            update(UserRow)
            .where(UserRow.id == user_id)
            .values(settings=settings)
            .returning(UserRow.settings)
        )
        result = (await self._session.execute(statement)).scalar_one_or_none()
        return dict(result) if result is not None else None
