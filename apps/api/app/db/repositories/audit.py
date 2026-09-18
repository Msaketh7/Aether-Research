"""Writing and reading the audit log.

**A transaction of its own, like the trace store's.** The row must survive the
request that produced it failing - and the most valuable rows are exactly the
ones written on a path that ends in an exception. A failed login raises
``Unauthenticated``, which rolls the request's transaction back; an audit row
written into that transaction would be rolled back with it, and the log would
contain successful logins only.

**A write never fails a request.** An audit row that cannot be stored is logged
as an error and the request continues. That is a real trade and the threat
model records it: failing closed would mean a database hiccup denies every
login in the deployment, which is a worse outcome than a gap in the trail that
the application log still covers.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select

from app.core.enums import AuditAction, AuditOutcome
from app.core.logging import get_logger, log_context
from app.db.models.audit import AuditLogRow
from app.db.session import Database

logger = get_logger(__name__)

#: Rows one query may return. Bounded like every list in this codebase.
MAX_AUDIT_ROWS = 200

#: Longest string kept in the context blob. The context is for a reason code or
#: a count, not for content.
_MAX_CONTEXT_VALUE = 200


class SqlAlchemyAuditLog:
    """Appends to ``audit_log``, and reads it back for an investigation."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def record(
        self,
        *,
        action: AuditAction,
        outcome: AuditOutcome,
        user_id: uuid.UUID | None = None,
        resource_type: str | None = None,
        resource_id: uuid.UUID | None = None,
        ip: str | None = None,
        user_agent: str = "",
        request_id: str | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            async with self._database.session() as session:
                session.add(
                    AuditLogRow(
                        user_id=user_id,
                        action=str(action),
                        outcome=str(outcome),
                        resource_type=resource_type,
                        resource_id=resource_id,
                        ip=ip,
                        user_agent=user_agent[:400],
                        request_id=request_id,
                        context=_bounded(context or {}),
                    )
                )
        except Exception as exc:
            # Never propagated: see the module docstring. Logged at error so
            # that a trail going quiet is visible in the place operators watch.
            logger.error(
                "an audit entry could not be written",
                extra=log_context(
                    {"action": str(action), "outcome": str(outcome), "error": str(exc)}
                ),
            )

    async def recent_for_user(
        self, user_id: uuid.UUID, *, limit: int = 50, since: dt.datetime | None = None
    ) -> list[AuditLogRow]:
        """One account's trail, newest first."""
        statement = (
            select(AuditLogRow)
            .where(AuditLogRow.user_id == user_id)
            .order_by(AuditLogRow.created_at.desc())
            .limit(min(limit, MAX_AUDIT_ROWS))
        )
        if since is not None:
            statement = statement.where(AuditLogRow.created_at >= since)
        async with self._database.session() as session:
            rows = list((await session.execute(statement)).scalars())
            for row in rows:
                session.expunge(row)
            return rows


def _bounded(context: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the context blob small and free of anything secret.

    Values are truncated rather than dropped, and the redaction the structured
    logger applies by key name is applied here too: an audit row is read by
    people and stored for a long time, so a token that reached it would outlive
    every rotation.
    """
    from app.core.logging import redact

    return {
        key: (value[:_MAX_CONTEXT_VALUE] if isinstance(value, str) else value)
        for key, value in ((key, redact(key, value)) for key, value in context.items())
    }
