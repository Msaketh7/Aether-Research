"""Session rows: the server-side half of a login.

The table holds a *hash* of each session token and never the token itself, so a
database leak hands over no live sessions (threat model 3.7). Every query here
is by that hash or by user, and every one of them is bounded.

A session is never deleted on logout - it is marked revoked, with the time.
Deleting it would erase the record that the session existed, which is the
question an audit answers. Expired rows are swept instead, well after they
stopped being usable.

Bound to the request's session like every other repository here, so that
registering an account and issuing its first session commit together: a session
row committed ahead of the user it points at would violate the foreign key, and
one committed after a failed registration would be a token for an account that
does not exist.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Executable

from app.db.models.user import SessionRow

#: Sessions one ``/auth/sessions`` response may list. A person has a handful of
#: devices; anything approaching this is a bug or an attack, and either way the
#: page must not try to render all of it.
MAX_LISTED_SESSIONS = 50


class SessionRepository:
    """Reads and writes ``sessions``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: uuid.UUID,
        token_hash: str,
        expires_at: dt.datetime,
        user_agent: str,
        ip: str | None,
    ) -> SessionRow:
        row = SessionRow(
            user_id=user_id,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=user_agent,
            ip=ip,
        )
        self._session.add(row)
        # Flushed rather than left pending: the caller needs the generated id
        # and created_at to describe the session it just issued.
        await self._session.flush()
        await self._session.refresh(row)
        return row

    async def find_active(self, token_hash: str, *, now: dt.datetime) -> SessionRow | None:
        """The live session for this token hash, or nothing.

        "Live" is checked in SQL rather than in Python so that an expired or
        revoked session cannot be resolved by a caller that forgets to look.
        """
        statement = select(SessionRow).where(
            SessionRow.token_hash == token_hash,
            SessionRow.revoked_at.is_(None),
            SessionRow.expires_at > now,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def list_active(
        self, user_id: uuid.UUID, *, now: dt.datetime, limit: int = MAX_LISTED_SESSIONS
    ) -> list[SessionRow]:
        statement = (
            select(SessionRow)
            .where(
                SessionRow.user_id == user_id,
                SessionRow.revoked_at.is_(None),
                SessionRow.expires_at > now,
            )
            .order_by(SessionRow.created_at.desc())
            .limit(limit)
        )
        return list((await self._session.execute(statement)).scalars())

    async def revoke(self, session_id: uuid.UUID, *, user_id: uuid.UUID, now: dt.datetime) -> bool:
        """Revoke one session. Scoped by user, so an id alone is not authority.

        Returns whether a live session was revoked, which is what separates
        "signed that device out" from "there was nothing to sign out".
        """
        statement = (
            update(SessionRow)
            .where(
                SessionRow.id == session_id,
                SessionRow.user_id == user_id,
                SessionRow.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        return bool(await self._affected(statement))

    async def revoke_all(
        self, user_id: uuid.UUID, *, now: dt.datetime, keep: uuid.UUID | None = None
    ) -> int:
        """Revoke every live session for a user, optionally sparing one.

        ``keep`` is how "sign out my other devices" leaves the caller signed in.
        """
        statement = update(SessionRow).where(
            SessionRow.user_id == user_id, SessionRow.revoked_at.is_(None)
        )
        if keep is not None:
            statement = statement.where(SessionRow.id != keep)
        return await self._affected(statement.values(revoked_at=now))

    async def count_active(self, user_id: uuid.UUID, *, now: dt.datetime) -> int:
        statement = select(func.count()).where(
            SessionRow.user_id == user_id,
            SessionRow.revoked_at.is_(None),
            SessionRow.expires_at > now,
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def revoke_beyond(self, user_id: uuid.UUID, *, keeping: int, now: dt.datetime) -> int:
        """Hold a user to a ceiling of live sessions, oldest revoked first.

        A ceiling rather than a refusal: someone signing in on a tenth device
        wants the tenth device, not an error. The cap exists so that a stolen
        credential used repeatedly cannot accumulate an unbounded set of live
        tokens, each of which would have to be revoked individually.
        """
        keepers = (
            select(SessionRow.id)
            .where(
                SessionRow.user_id == user_id,
                SessionRow.revoked_at.is_(None),
                SessionRow.expires_at > now,
            )
            .order_by(SessionRow.created_at.desc())
            .limit(keeping)
        )
        statement = (
            update(SessionRow)
            .where(
                SessionRow.user_id == user_id,
                SessionRow.revoked_at.is_(None),
                SessionRow.id.not_in(keepers),
            )
            .values(revoked_at=now)
        )
        return await self._affected(statement)

    async def delete_expired(self, *, before: dt.datetime, limit: int = 1000) -> int:
        """Sweep rows that stopped being usable long ago.

        Bounded like every other statement here: an unbounded DELETE on a table
        that grows with every login is a lock nobody planned for.
        """
        doomed = select(SessionRow.id).where(SessionRow.expires_at < before).limit(limit)
        return await self._affected(delete(SessionRow).where(SessionRow.id.in_(doomed)))

    async def _affected(self, statement: Executable) -> int:
        """Rows an UPDATE or DELETE touched.

        ``rowcount`` lives on the DBAPI cursor result, which SQLAlchemy's
        typing does not narrow to from ``execute``; the annotation here is what
        makes that visible rather than a cast at four call sites.
        """
        result: CursorResult[Any] = await self._session.execute(statement)  # type: ignore[assignment]
        return int(result.rowcount or 0)
