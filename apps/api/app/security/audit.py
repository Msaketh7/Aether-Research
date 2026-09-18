"""The request-scoped audit recorder.

Where a row comes from is the same for every entry - the caller's address, the
user agent, the request id - and where it goes is the same store. This binds
the first to the second once per request, so a call site names only the thing
that happened:

    await trail.record(AuditAction.RUN_CREATED, resource_id=run.id)

Auditing is done at the endpoint rather than inside the service layer. It is a
statement about a *request*: who asked, from where, and what the API decided.
The service does not know any of that, and the one thing an audit trail cannot
afford is to be assembled from guesses.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.core.enums import AuditAction, AuditOutcome
from app.db.repositories.audit import SqlAlchemyAuditLog


@dataclass(frozen=True, slots=True)
class AuditTrail:
    """One request's worth of audit context, plus the store to append to."""

    log: SqlAlchemyAuditLog
    ip: str | None
    user_agent: str
    request_id: str | None

    async def record(
        self,
        action: AuditAction,
        *,
        outcome: AuditOutcome = AuditOutcome.SUCCESS,
        user_id: uuid.UUID | None = None,
        resource_type: str | None = None,
        resource_id: uuid.UUID | None = None,
        **context: Any,
    ) -> None:
        await self.log.record(
            action=action,
            outcome=outcome,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            ip=self.ip,
            user_agent=self.user_agent,
            request_id=self.request_id,
            context=context,
        )

    async def failure(
        self,
        action: AuditAction,
        *,
        user_id: uuid.UUID | None = None,
        resource_type: str | None = None,
        resource_id: uuid.UUID | None = None,
        **context: Any,
    ) -> None:
        """A refused or failed attempt. The half of the log that gets read."""
        await self.record(
            action,
            outcome=AuditOutcome.FAILURE,
            user_id=user_id,
            resource_type=resource_type,
            resource_id=resource_id,
            **context,
        )
