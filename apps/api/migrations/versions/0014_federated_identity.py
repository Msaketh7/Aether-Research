"""Federated identity and rotating refresh tokens (Phase 26, ADR 0022).

Single sign-on replaced opaque sessions with signed tokens, and three schema
changes follow from that decision.

**`sessions.token_hash` is gone.** It held the SHA-256 of an opaque session
token, and there is no longer such a thing: the credential a caller presents is
a JWT, and the row's *id* is what appears in it as `sid`. Keeping a `NOT NULL
UNIQUE` column that nothing can populate would have meant inventing a value to
satisfy it. What the table is still for is the two jobs a token cannot do -
showing a person their devices, and being the durable record that `revoked_at`
stops a session being renewed.

**`sessions.provider` is new**, so a session can say which issuer admitted it.
Defaulted to `local` rather than left nullable, because every row that exists
when this runs was created by password sign-in and that is not an unknown.

**Two new tables.** `identities` links a provider's subject to an account, keyed
on `(provider, subject)` and never on email - see the model's docstring for why
linking on an address is an account-takeover bug rather than a convenience.
`refresh_tokens` holds one rotating family per sign-in; `family_id` is what
makes reuse detectable, because discovering a second use of an already-used
token has to revoke the whole lineage rather than the single row.

**The downgrade restores `token_hash` as nullable.** It cannot restore the
values - they were hashes of tokens that no longer exist - and it must not
invent them. A downgrade here signs everybody out, which is the honest outcome
of reverting the decision that changed what a session is.

Revision ID: 0014_federated_identity
Revises: 0013_dispatch_clock
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_federated_identity"
down_revision: str | None = "0013_dispatch_clock"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'local'"),
        ),
    )
    # The unique index goes with the column it was built for.
    op.drop_column("sessions", "token_hash")

    op.create_table(
        "identities",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("connection", sa.String(length=32), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        # The constraint that stops one provider identity being attached to two
        # accounts, which is the database half of "link on subject, not email".
        sa.UniqueConstraint("provider", "subject", name="uq_identities_provider_subject"),
    )
    op.create_index("ix_identities_user_id", "identities", ["user_id"])
    # `TimestampMixin` declares `created_at` indexed on every table it is
    # mixed into, so the migration owes one here too. Caught by
    # `test_the_migrations_and_the_models_agree`, which is what that test
    # is for.
    op.create_index("ix_identities_created_at", "identities", ["created_at"])

    op.create_table(
        "refresh_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("family_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])
    op.create_index("ix_refresh_tokens_session_id", "refresh_tokens", ["session_id"])
    op.create_index("ix_refresh_tokens_created_at", "refresh_tokens", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_created_at", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_session_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_family_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")

    op.drop_index("ix_identities_created_at", table_name="identities")
    op.drop_index("ix_identities_user_id", table_name="identities")
    op.drop_table("identities")

    op.drop_column("sessions", "provider")
    # Nullable, and without the unique index: there is nothing to put in it.
    # Restoring the NOT NULL would need a fabricated hash for every live
    # session, and a fabricated credential is worse than a signed-out user.
    op.add_column("sessions", sa.Column("token_hash", sa.Text(), nullable=True))
