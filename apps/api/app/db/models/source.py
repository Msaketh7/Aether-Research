"""Sources, documents and document chunks.

The provenance chain starts here. A ``Source`` is the reference; a ``Document``
is its cleaned text plus a pointer to the raw bytes in object storage; a
``DocumentChunk`` is a searchable slice with a keyword index and (from the
pgvector migration) an embedding.

``content_hash`` on both source and document is what makes ingestion idempotent
and duplicate detection cheap: the same page fetched twice produces the same
digest and is collapsed rather than counted twice as corroboration.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import TYPE_CHECKING, Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Computed,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import SourceType
from app.db.base import Base, TimestampMixin, fk_uuid, uuid_pk
from app.db.models.research import enum_check

#: Dimension of the embedding column. Fixed in the schema because pgvector
#: indexes a declared width; changing embedding model families therefore means
#: a migration and a re-index, not a configuration flip. text-embedding-3-small
#: and most open alternatives are 1536.
EMBEDDING_DIMENSIONS = 1536

if TYPE_CHECKING:
    from app.db.models.evidence import EvidenceRow
    from app.db.models.research import ResearchRunRow


class SourceRow(Base, TimestampMixin):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("research_runs.id", ondelete="CASCADE"), nullable=False
    )

    url: Mapped[str] = mapped_column(Text, nullable=False)
    #: After canonicalisation. Two sources sharing this are the same page.
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    publisher: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    author: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[dt.datetime | None]
    #: When Aether fetched it. Distinct from published_at and never inferred -
    #: a citation is only meaningful with both.
    accessed_at: Mapped[dt.datetime] = mapped_column(nullable=False)

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    credibility_score: Mapped[float] = mapped_column(
        Numeric(3, 2), nullable=False, server_default=text("0.50")
    )
    credibility_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: Groups near-duplicates. Not a foreign key: the cluster is derived, and a
    #: separate table would be a join for something only ever read with the row.
    dedup_cluster_id: Mapped[uuid.UUID | None] = fk_uuid()
    relevance_score: Mapped[float] = mapped_column(
        Numeric(3, 2), nullable=False, server_default=text("0.50")
    )
    task_external_id: Mapped[str | None] = mapped_column(String(80))
    claim_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    excerpt: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))

    run: Mapped[ResearchRunRow] = relationship(back_populates="sources")
    documents: Mapped[list[DocumentRow]] = relationship(
        back_populates="source", cascade="all, delete-orphan", passive_deletes=True
    )
    evidence: Mapped[list[EvidenceRow]] = relationship(back_populates="source")

    __table_args__ = (
        enum_check("source_type", SourceType, "sources_source_type"),
        # The sources page: this run's sources in discovery order.
        Index("ix_sources_run_id_created_at", "run_id", "created_at"),
        # Deduplication within a run.
        Index("ix_sources_run_id_content_hash", "run_id", "content_hash"),
    )


class DocumentRow(Base, TimestampMixin):
    """The cleaned text of a source, plus a pointer to the raw bytes in S3."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = uuid_pk()
    source_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    storage_key: Mapped[str | None] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(
        String(120), nullable=False, server_default=text("'text/html'")
    )
    raw_size_bytes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    #: Boilerplate-stripped text. Evidence span offsets point into this exact
    #: string, so it must never be rewritten in place.
    normalized_content: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(10))
    extraction_method: Mapped[str] = mapped_column(
        String(50), nullable=False, server_default=text("'unknown'")
    )
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    #: sha256 of the normalised content. Unique: the idempotent ingestion key.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    source: Mapped[SourceRow] = relationship(back_populates="documents")
    chunks: Mapped[list[DocumentChunkRow]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class DocumentChunkRow(Base, TimestampMixin):
    """One searchable slice of a document.

    The ``embedding`` column and its HNSW index are added by the pgvector
    migration, which is separate because the extension is a server-side
    prerequisite that a managed Postgres may need enabled first.
    """

    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = uuid_pk()
    document_id: Mapped[uuid.UUID] = fk_uuid(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    #: Dense vector for meaning-search. Added by the pgvector revision, and
    #: nullable because a chunk exists from the moment it is parsed, before the
    #: embedding call that fills this in has run.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    embedding_model: Mapped[str | None] = mapped_column(String(120))
    #: Generated by Postgres from `content`, so it can never drift out of sync
    #: with the text it indexes.
    tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True)
    )
    #: Section, page, character offsets, source_type - the metadata filters
    #: retrieval applies before ranking.
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    document: Mapped[DocumentRow] = relationship(back_populates="chunks")

    __table_args__ = (
        Index(
            "uq_document_chunks_document_id_chunk_index",
            "document_id",
            "chunk_index",
            unique=True,
        ),
        # Lexical half of hybrid retrieval (ADR 0004).
        Index("ix_document_chunks_tsv", "tsv", postgresql_using="gin"),
        # Metadata filtering before ranking.
        Index("ix_document_chunks_metadata", "metadata", postgresql_using="gin"),
    )
