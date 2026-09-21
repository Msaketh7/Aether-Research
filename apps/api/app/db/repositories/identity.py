"""Linked upstream identities.

The lookup every federated sign-in performs is `(provider, subject)`, and that
pair is the table's unique key. Nothing here looks an identity up by email,
and that absence is the point: a provider's subject is stable and unique, an
address is neither, and resolving a sign-in by address is how federated auth
becomes account takeover.

Where the address *is* used - attaching a first provider sign-in to an existing
local account - it is the caller's decision, gated by a setting that is off by
default, and it requires the provider to have marked the address verified. That
rule lives in `app.auth.federation`, not here, because it is policy rather than
storage.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import IdentityRow

#: Identities one account may list. A person links a handful; a page must not
#: try to render an unbounded set.
MAX_LISTED_IDENTITIES = 50


class IdentityRepository:
    """Reads and writes ``identities``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find(self, *, provider: str, subject: str) -> IdentityRow | None:
        result = await self._session.execute(
            select(IdentityRow).where(
                IdentityRow.provider == provider,
                IdentityRow.subject == subject,
            )
        )
        return result.scalar_one_or_none()

    async def link(
        self,
        *,
        user_id: uuid.UUID,
        provider: str,
        subject: str,
        connection: str | None,
        email: str | None,
        now: dt.datetime,
    ) -> IdentityRow | None:
        """Attach a provider identity to an account.

        Returns ``None`` when the pair is already linked to somebody. The
        database's unique constraint is what decides that, not a prior SELECT:
        two concurrent first sign-ins through the same provider identity would
        both pass a check-then-insert, and the second must lose rather than
        create a duplicate.
        """
        row = IdentityRow(
            user_id=user_id,
            provider=provider,
            subject=subject,
            connection=connection,
            email=email,
            last_used_at=now,
        )
        try:
            # A SAVEPOINT around the insert, for the reason
            # `UserRepository.create` uses one: a unique violation poisons the
            # enclosing transaction, and a plain `rollback()` here would throw
            # away the whole request - including the user row created moments
            # earlier by a first federated sign-in, and the audit row that has
            # still to be written.
            async with self._session.begin_nested():
                self._session.add(row)
                await self._session.flush()
        except IntegrityError:
            return None
        await self._session.refresh(row)
        return row

    async def touch(self, identity_id: uuid.UUID, *, now: dt.datetime) -> None:
        """Record that this identity was just used to sign in."""
        await self._session.execute(
            update(IdentityRow).where(IdentityRow.id == identity_id).values(last_used_at=now)
        )

    async def list_for_user(self, user_id: uuid.UUID) -> list[IdentityRow]:
        result = await self._session.execute(
            select(IdentityRow)
            .where(IdentityRow.user_id == user_id)
            .order_by(IdentityRow.created_at)
            .limit(MAX_LISTED_IDENTITIES)
        )
        return list(result.scalars().all())

    async def count_for_user(self, user_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(IdentityRow.id).where(IdentityRow.user_id == user_id)
        )
        return len(list(result.scalars().all()))

    async def unlink(self, identity_id: uuid.UUID, *, user_id: uuid.UUID) -> bool:
        """Remove a link, scoped by owner.

        Scoped in SQL rather than checked afterwards, so another person's
        identity id is simply not found - the caller learns nothing about
        whether it exists.
        """
        row = await self._session.get(IdentityRow, identity_id)
        if row is None or row.user_id != user_id:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True
