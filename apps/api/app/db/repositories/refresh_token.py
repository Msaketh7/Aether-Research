"""Refresh token rows, and the reuse detection built on them.

Rotation without detection is bookkeeping. The point of minting a replacement
on every use is that the old one must then never be used again - so a second
presentation of a token that already has `used_at` set is evidence that two
parties hold it, and the only safe reading is that one of them stole it.

Which one cannot be determined from here, so the whole family is revoked and
the person signs in again. That is the trade this makes deliberately: a rare
false positive (a client that retried a refresh after a dropped response)
costs one re-authentication, while the alternative - assuming the second use
is the legitimate one - lets a thief rotate alongside the victim indefinitely,
which is the exact attack rotation exists to catch.

`family_id` is what makes "the whole family" addressable. Every token
descended from one sign-in carries it, so revoking a lineage is one indexed
UPDATE rather than a walk back through a parent chain.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Executable

from app.core.logging import get_logger
from app.db.models.user import RefreshTokenRow, SessionRow
from app.db.session import Database

logger = get_logger(__name__)


class RefreshTokenRepository:
    """Reads and writes ``refresh_tokens``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        session_id: uuid.UUID,
        token_hash: str,
        family_id: uuid.UUID,
        expires_at: dt.datetime,
    ) -> RefreshTokenRow:
        row = RefreshTokenRow(
            session_id=session_id,
            token_hash=token_hash,
            family_id=family_id,
            expires_at=expires_at,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def find(self, token_hash: str) -> RefreshTokenRow | None:
        """The row for this token, used or not.

        Deliberately **not** filtered to unused rows. A lookup that skipped
        used ones would make a replayed token indistinguishable from an unknown
        one, and reuse detection depends on telling those apart: the first is
        a theft signal, the second is somebody pasting nonsense.
        """
        result = await self._session.execute(
            select(RefreshTokenRow).where(RefreshTokenRow.token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def mark_used(self, token_id: uuid.UUID, *, now: dt.datetime) -> bool:
        """Consume a token, if it has not been consumed already.

        The `used_at IS NULL` predicate is in the UPDATE rather than checked
        beforehand, so that two refreshes racing on the same token cannot both
        succeed: Postgres serialises the write and exactly one reports a row.
        A check-then-write would let both through, which is the race a thief
        and a victim refreshing simultaneously produce.
        """
        return (
            await self._affected(
                update(RefreshTokenRow)
                .where(RefreshTokenRow.id == token_id, RefreshTokenRow.used_at.is_(None))
                .values(used_at=now)
            )
            > 0
        )

    async def revoke_family(self, family_id: uuid.UUID, *, now: dt.datetime) -> int:
        """Revoke every token descended from one sign-in."""
        return await self._affected(
            update(RefreshTokenRow)
            .where(RefreshTokenRow.family_id == family_id, RefreshTokenRow.revoked_at.is_(None))
            .values(revoked_at=now)
        )

    async def revoke_for_session(self, session_id: uuid.UUID, *, now: dt.datetime) -> int:
        """Revoke the tokens belonging to one session.

        Called when a device is signed out: the access token is stopped by the
        revocation index, and this is what stops the refresh token quietly
        minting a new one.
        """
        return await self._affected(
            update(RefreshTokenRow)
            .where(
                RefreshTokenRow.session_id == session_id,
                RefreshTokenRow.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )

    async def _affected(self, statement: Executable) -> int:
        """Rows an UPDATE touched.

        `rowcount` is on `CursorResult`, which is what a DML statement returns;
        the declared `Result` does not carry it. The same narrowing
        `SessionRepository` does, for the same reason.
        """
        result = await self._session.execute(statement)
        return cast("CursorResult[Any]", result).rowcount or 0


class DurableRevocation:
    """Revoking a compromised family in a transaction of its own.

    **This exists because of how the request ends.** Reuse detection concludes
    in a 401, and a 401 rolls the request's transaction back - which would undo
    the very revocation the detection just performed. The result was a detector
    that detected and then quietly changed nothing: the 401 was returned, the
    rows stayed live, and the thief's next rotation succeeded.

    The same shape, and the same reason, as `SqlAlchemyAuditLog`: a write that
    must survive the refusal it accompanies gets its own session. Caught by
    `test_reusing_a_rotated_refresh_token_signs_the_whole_family_out`, which
    asserts the rows rather than the status code - a test that only checked for
    a 401 would have passed against the broken version.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def revoke_family(
        self, *, family_id: uuid.UUID, session_id: uuid.UUID, now: dt.datetime
    ) -> None:
        """Revoke every token in a family, and the session behind it."""
        try:
            async with self._database.session() as session:
                await session.execute(
                    update(RefreshTokenRow)
                    .where(
                        RefreshTokenRow.family_id == family_id,
                        RefreshTokenRow.revoked_at.is_(None),
                    )
                    .values(revoked_at=now)
                )
                await session.execute(
                    update(SessionRow)
                    .where(SessionRow.id == session_id, SessionRow.revoked_at.is_(None))
                    .values(revoked_at=now)
                )
        except Exception:
            # Logged and swallowed: the caller is already refusing the request,
            # and raising here would replace a correct 401 with a 500. The
            # access token still expires on its own, and the consumed refresh
            # token is already marked used, so the failure narrows the response
            # rather than reopening the session.
            logger.exception(
                "could not revoke a compromised token family",
                extra={"family_id": str(family_id)},
            )
