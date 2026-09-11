"""Files a user uploaded, and the runs they are attached to (Phase 7).

An upload is the user's file, not yet anyone's evidence. It exists before the
run that uses it - the contract names uploads in ``document_ids`` when a run is
created - so it cannot hang off a run the way a source does. When a run is
executed, each attached upload is ingested into *that run's* corpus as an
ordinary source, document and chunks (ADR 0012). The run then reads exactly like
one whose sources were fetched from the web, and no provenance chain reaches
across users.
"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import DocumentFormat
from app.db.base import Base, TimestampMixin, fk_uuid, uuid_pk
from app.db.models.research import enum_check


class UploadRow(Base, TimestampMixin):
    __tablename__ = "uploads"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = fk_uuid(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    #: As the client named it, cleaned for display. Never used to build a path
    #: or a key: the key is derived from the content hash.
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The canonical type for ``format``, not the client's string - that string
    #: is attacker-controlled and was checked against the bytes, not trusted.
    mime_type: Mapped[str] = mapped_column(String(120), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: sha256 of the bytes as uploaded. With ``user_id``, the idempotency key.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    #: The charset the client declared for a text format, so ingestion decodes
    #: the bytes the way the uploader said they were encoded.
    charset: Mapped[str | None] = mapped_column(String(40))

    __table_args__ = (
        enum_check("format", DocumentFormat, "uploads_format"),
        CheckConstraint("size_bytes > 0", name="ck_uploads_size_positive"),
        # The same bytes from the same user are one upload. Per user, never
        # global: a global key would tell one user that another had already
        # uploaded a given confidential file.
        Index("uq_uploads_user_id_content_hash", "user_id", "content_hash", unique=True),
        # The file list: this user's uploads, newest first, with a total order
        # for keyset pagination.
        Index("ix_uploads_user_id_created_at_id", "user_id", text("created_at DESC"), "id"),
    )


class ResearchRunUploadRow(Base, TimestampMixin):
    """An upload a run was created with (``document_ids``).

    Recorded at creation, in the same transaction as the run, so the worker that
    executes the run later knows exactly which files the user asked it to read.
    """

    __tablename__ = "research_run_uploads"

    run_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("research_runs.id", ondelete="CASCADE"), primary_key=True
    )
    upload_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("uploads.id", ondelete="CASCADE"), primary_key=True
    )
