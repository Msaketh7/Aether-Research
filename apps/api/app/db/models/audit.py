"""The audit log.

The threat model's answer to repudiation: a durable record of who authenticated
and who changed what, separate from the access log because an access log is
rotated and sampled and this is neither.

**Append-only by construction.** Nothing in the repository above this updates or
deletes a row, and the table has no mutable column to update - every field is
what was true at the moment the row was written. A logout does not edit the
login row; it adds one.

**No foreign key, deliberately.** ``user_id`` and ``resource_id`` are
*identifiers*, not references: this table records what happened, and what
happened stays true after the row it names is gone. A foreign key would give
the log two ways to fail at exactly the wrong moments. It would reject the row
for an action whose own transaction has not committed yet - registration
writes its user and its audit entry in different transactions on purpose, and
with the key in place the record of every sign-up was silently dropped (found
by running it). And it would force a choice between cascading the trail away
with the account, which is what an audit log must never do, or nulling the
actor, which erases the answer to "who" just when someone is asking.

The actor is null only when there genuinely was none - a failed login has no
authenticated user, which is what makes it failed.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Index, String, Text, text
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, NormalisedUUID, TimestampMixin, uuid_pk


class AuditLogRow(Base, TimestampMixin):
    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = uuid_pk()

    #: Who did it. Null for an unauthenticated attempt; otherwise the account's
    #: id, kept whether or not that account still exists. Indexed, because "what
    #: did this account do" is the query an investigation opens with.
    user_id: Mapped[uuid.UUID | None] = mapped_column(NormalisedUUID(), index=True)

    #: An ``AuditAction`` value. Stored as text like every other closed
    #: vocabulary in this schema (TDD 7.2), so a new action is a deploy rather
    #: than a migration and an old row keeps meaning what it meant.
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    #: An ``AuditOutcome``. A refused action is the more interesting half of an
    #: audit log, so it is a recorded outcome and not an absent row.
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)

    #: What was acted on, when there is one. Not a foreign key either, and for
    #: the same reason: the row must survive the object being deleted, which is
    #: when the record of who deleted it matters most.
    resource_type: Mapped[str | None] = mapped_column(String(32))
    resource_id: Mapped[uuid.UUID | None] = mapped_column(NormalisedUUID())

    #: Where it came from, resolved through the declared proxy hops
    #: (``app.security.forwarded``) rather than read off a header.
    ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    #: The request id echoed to the caller and carried on every log line, so an
    #: audit row can be joined to the logs of the request that produced it.
    request_id: Mapped[str | None] = mapped_column(String(64))

    #: Bounded, non-secret detail: a reason code, a count, a run's mode. Never
    #: a password, a token, or the content of anything a user wrote.
    context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        # "What did this account do", newest first - the question an
        # investigation opens with.
        Index("ix_audit_log_user_id_created_at", "user_id", "created_at"),
        # "Who has been failing to sign in", across accounts.
        Index("ix_audit_log_action_created_at", "action", "created_at"),
    )
