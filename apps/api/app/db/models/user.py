"""Users and sessions.

Populated by the accounts system in Phase 20; the tables exist now because
every research object is owned by a user and that foreign key is what makes
the authorisation rules enforceable in the database rather than only in code.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import CITEXT, INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UpdatedAtMixin, fk_uuid, uuid_pk

if TYPE_CHECKING:
    from app.db.models.research import ResearchRunRow


class UserRow(Base, TimestampMixin, UpdatedAtMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = uuid_pk()
    # CITEXT so that Ada@example.com and ada@example.com cannot both register.
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(String(200), nullable=False, server_default=text("''"))
    role: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'user'"))
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    last_login_at: Mapped[dt.datetime | None]

    runs: Mapped[list[ResearchRunRow]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    sessions: Mapped[list[SessionRow]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class SessionRow(Base, TimestampMixin):
    """One active login. Listed in /settings so a device can be signed out."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = fk_uuid(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Hashed, never the token itself: a database leak must not hand over live
    # sessions (docs/threat-model.md section 3.7).
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    user_agent: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    ip: Mapped[str | None] = mapped_column(INET)
    expires_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[dt.datetime | None]

    user: Mapped[UserRow] = relationship(back_populates="sessions")

    __table_args__ = (
        # The session-validation query: this user's sessions, newest first.
        Index("ix_sessions_user_id_expires_at", "user_id", "expires_at"),
    )
