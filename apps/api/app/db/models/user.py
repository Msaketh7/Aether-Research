"""Users and sessions.

Populated by the accounts system in Phase 20; the tables exist now because
every research object is owned by a user and that foreign key is what makes
the authorisation rules enforceable in the database rather than only in code.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, text
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
    identities: Mapped[list[IdentityRow]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class SessionRow(Base, TimestampMixin):
    """One device's login.

    Since ADR 0022 this holds no credential. The session *id* is what appears
    in every access token as `sid`, and renewal happens through the
    `refresh_tokens` rows hanging off it. What the row is for is the two things
    a token cannot do: letting a person see their devices, and making
    `revoked_at` the durable record that stops a session being renewed.
    """

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = fk_uuid(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Which issuer authenticated this session - `local`, `auth0`, `supabase`.
    # Recorded so the session list can say how a device got in, and so an audit
    # trail can answer "was this person ever admitted without a password?".
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'local'")
    )
    user_agent: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    ip: Mapped[str | None] = mapped_column(INET)
    expires_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    revoked_at: Mapped[dt.datetime | None]

    user: Mapped[UserRow] = relationship(back_populates="sessions")
    refresh_tokens: Mapped[list[RefreshTokenRow]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        # The session-validation query: this user's sessions, newest first.
        Index("ix_sessions_user_id_expires_at", "user_id", "expires_at"),
    )


class IdentityRow(Base, TimestampMixin):
    """One upstream identity linked to an account.

    **The unique key is (provider, subject), not the email.** A provider's
    subject is stable and unique; an email is neither. Addresses get changed at
    the provider, recycled after an account is deleted, and asserted without
    verification by providers that do not check - so linking on one is how
    federated sign-in turns into account takeover. The address is stored
    because it is useful to display, and it is never looked up.
    """

    __tablename__ = "identities"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = fk_uuid(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    connection: Mapped[str | None] = mapped_column(String(32))
    # Not CITEXT and not unique: this is a label, not a key. See the docstring.
    email: Mapped[str | None] = mapped_column(Text)
    last_used_at: Mapped[dt.datetime | None]

    user: Mapped[UserRow] = relationship(back_populates="identities")

    __table_args__ = (
        # The lookup every federated sign-in performs, and the constraint that
        # stops one provider identity being attached to two accounts.
        UniqueConstraint("provider", "subject", name="uq_identities_provider_subject"),
        Index("ix_identities_user_id", "user_id"),
    )


class RefreshTokenRow(Base, TimestampMixin):
    """One refresh token, in a rotating family.

    Rotation means every use mints a replacement and marks this row used. The
    `family_id` is what makes reuse detectable: all the tokens descended from
    one sign-in share it, so discovering a second use of an already-used token
    identifies the whole lineage to revoke rather than just the one row.

    Hashed at rest for the same reason the old session tokens were: a database
    leak must not hand over the ability to mint access tokens
    (docs/threat-model.md section 3.7).
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = uuid_pk()
    session_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    #: Shared by every token descended from one sign-in.
    family_id: Mapped[uuid.UUID] = fk_uuid(nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(nullable=False)
    #: When this token was exchanged. A second exchange of a row that already
    #: has this set is the theft signal, not an error to retry.
    used_at: Mapped[dt.datetime | None]
    revoked_at: Mapped[dt.datetime | None]

    session: Mapped[SessionRow] = relationship(back_populates="refresh_tokens")

    __table_args__ = (
        Index("ix_refresh_tokens_family_id", "family_id"),
        Index("ix_refresh_tokens_session_id", "session_id"),
    )
