"""Document ingestion: uploads, run attachments, per-source document identity.

All relational, which is why this revision descends from the core schema and not
from the pgvector revision (see "Two lines" below).

* ``uploads``: a user's files. They exist before any run uses them - the API
  contract names them in ``document_ids`` when a run is created - so they are
  owned by the user, not by a run.
* ``research_run_uploads``: which uploads a run was created with, written in the
  same transaction as the run.
* ``documents``: the unique key moves from ``content_hash`` to
  ``(source_id, content_hash)``. A global key allowed one copy of a document per
  database, so a second user's run could not ingest a PDF the first had already
  ingested - and had the copy been shared instead, ``evidence.document_id``
  cascades, so one user deleting a run would delete another user's evidence.
  Adds ``metadata`` for format-specific facts.

## Two lines

From here, migrations form two branches off ``0001_core_schema``:

* ``core`` - relational changes, which any Postgres can apply;
* ``vector`` - changes that need the pgvector extension (0002, 0004).

0002 was split out so that a missing extension fails in one isolated place.
Chaining relational revisions *after* it would undo that: every later table would
then require pgvector, and the relational schema could no longer be built or
tested where the extension is not installed. ``alembic upgrade heads`` applies
both lines; ``alembic upgrade core@head`` applies only this one.

Revision ID: 0003_document_ingestion
Revises: 0001_core_schema
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0003_document_ingestion"
down_revision: str | None = "0001_core_schema"
branch_labels: str | Sequence[str] | None = ("core",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "uploads",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("format", sa.String(length=20), nullable=False),
        sa.Column("mime_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("charset", sa.String(length=40), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "format IN ('pdf', 'html', 'markdown', 'text')",
            name=op.f("ck_uploads_uploads_format"),
        ),
        sa.CheckConstraint("size_bytes > 0", name=op.f("ck_uploads_ck_uploads_size_positive")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_uploads_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_uploads")),
    )
    op.create_index(op.f("ix_uploads_created_at"), "uploads", ["created_at"], unique=False)
    op.create_index(op.f("ix_uploads_user_id"), "uploads", ["user_id"], unique=False)
    op.create_index(
        "uq_uploads_user_id_content_hash", "uploads", ["user_id", "content_hash"], unique=True
    )
    op.create_index(
        "ix_uploads_user_id_created_at_id",
        "uploads",
        ["user_id", sa.literal_column("created_at DESC"), "id"],
        unique=False,
    )

    op.create_table(
        "research_run_uploads",
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("upload_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["research_runs.id"],
            name=op.f("fk_research_run_uploads_run_id_research_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["uploads.id"],
            name=op.f("fk_research_run_uploads_upload_id_uploads"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("run_id", "upload_id", name=op.f("pk_research_run_uploads")),
    )
    op.create_index(
        op.f("ix_research_run_uploads_created_at"),
        "research_run_uploads",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_research_run_uploads_run_id"), "research_run_uploads", ["run_id"], unique=False
    )
    op.create_index(
        op.f("ix_research_run_uploads_upload_id"),
        "research_run_uploads",
        ["upload_id"],
        unique=False,
    )

    op.drop_constraint(op.f("uq_documents_content_hash"), "documents", type_="unique")
    op.create_index(
        "uq_documents_source_id_content_hash",
        "documents",
        ["source_id", "content_hash"],
        unique=True,
    )
    op.add_column(
        "documents",
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("documents", "metadata")
    op.drop_index("uq_documents_source_id_content_hash", table_name="documents")
    # Fails if two sources now hold the same content, which is the state this
    # revision exists to allow. Refusing is correct: deleting one user's copy to
    # make the old constraint fit would be data loss dressed up as a rollback.
    op.create_unique_constraint(op.f("uq_documents_content_hash"), "documents", ["content_hash"])

    op.drop_index(op.f("ix_research_run_uploads_upload_id"), table_name="research_run_uploads")
    op.drop_index(op.f("ix_research_run_uploads_run_id"), table_name="research_run_uploads")
    op.drop_index(op.f("ix_research_run_uploads_created_at"), table_name="research_run_uploads")
    op.drop_table("research_run_uploads")

    op.drop_index("ix_uploads_user_id_created_at_id", table_name="uploads")
    op.drop_index("uq_uploads_user_id_content_hash", table_name="uploads")
    op.drop_index(op.f("ix_uploads_user_id"), table_name="uploads")
    op.drop_index(op.f("ix_uploads_created_at"), table_name="uploads")
    op.drop_table("uploads")
