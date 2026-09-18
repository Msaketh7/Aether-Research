"""The audit log (Phase 20).

The threat model's repudiation control, which until now was a claim in a
document with no table behind it: a durable record of who authenticated and who
changed what. Separate from the access log because an access log is sampled and
rotated and this is neither, and separate from `agent_runs` because that traces
what the *system* did inside a run, not what a *person* asked it to do.

Append-only by construction. Nothing above this writes an UPDATE or a DELETE,
and there is no mutable column to update: every field records what was true at
the moment the row was written. A logout does not edit the login row, it adds
one.

**No foreign key on ``user_id``.** It is an identifier, not a reference: what
happened stays true after the row it names is gone. A key here would reject the
row for an action whose own transaction has not committed yet - registration
writes its user and its audit entry in separate transactions on purpose, and
with the key in place every sign-up's record was silently dropped - and it would
force a choice between cascading the trail away with the account or nulling the
actor, which erases the answer to "who". ``user_id`` is null only when there
genuinely was no authenticated user, which is what a failed login is.

The rest of the schema is untouched: `users` and `sessions` were created by
0001 with the columns this phase finally writes (`password_hash`, `token_hash`,
`expires_at`, `revoked_at`), so authentication needed no migration of its own.

Revision ID: 0012_audit_log
Revises: 0011_uncosted_calls
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_audit_log"
down_revision: str | None = "0011_uncosted_calls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=True),
        sa.Column("resource_id", sa.UUID(), nullable=True),
        sa.Column("ip", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(op.f("ix_audit_log_created_at"), "audit_log", ["created_at"])
    op.create_index(op.f("ix_audit_log_user_id"), "audit_log", ["user_id"])
    # The two questions an investigation actually asks: what did this account
    # do, and who has been failing to sign in.
    op.create_index("ix_audit_log_user_id_created_at", "audit_log", ["user_id", "created_at"])
    op.create_index("ix_audit_log_action_created_at", "audit_log", ["action", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_log_action_created_at", table_name="audit_log")
    op.drop_index("ix_audit_log_user_id_created_at", table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_user_id"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_created_at"), table_name="audit_log")
    op.drop_table("audit_log")
