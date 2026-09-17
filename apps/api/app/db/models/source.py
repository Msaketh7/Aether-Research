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
    CheckConstraint,
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

#: Text-search configuration for the lexical half of hybrid retrieval. The
#: generated ``tsv`` column below is built with it, and every query must parse
#: its terms with the same one: ``to_tsvector('english', ...)`` stems "pricing"
#: to "price", and a query parsed as ``simple`` would look for "pricing" and
#: find nothing. Naming it once is what stops the two from drifting apart -
#: silently, because a mismatch returns no rows rather than an error.
#:
#: English is the configuration, not a per-document choice, because the column
#: is generated and Postgres requires a constant there. Documents in other
#: languages are still indexed and still match on exact words; they lose
#: stemming, which the language filter and the dense half both mitigate.
TEXT_SEARCH_CONFIG = "english"

#: Dimension of the embedding column, and of the one embedding model the
#: registry declares (``nomic-embed-text``). Fixed in the schema because pgvector
#: indexes a declared width, so changing embedding model families means a
#: migration and a re-embed, not a configuration flip. The ingestion pipeline
#: compares this with the configured model's declared width when it is built,
#: so a mismatch fails at startup rather than on the first write (ADR 0012).
EMBEDDING_DIMENSIONS = 768

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
    #: Which rule collapsed this source into its cluster (``app.evidence.dedup``).
    #: NULL until the run's evidence has been projected. Stored because it cannot
    #: be recomputed from the row - the excerpts it was judged on may have been
    #: re-ingested since - and because a reader shown "3 duplicates" deserves to
    #: know whether that was a digest match or a judgement about overlapping text.
    dedup_reason: Mapped[str | None] = mapped_column(String(20))
    #: How well this source answers the run's question. Nullable because it has
    #: no default that would be true: a placeholder here is displayed to a reader
    #: as a measurement, so "not measured" has to be representable (0007).
    relevance_score: Mapped[float | None] = mapped_column(Numeric(3, 2))
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
        CheckConstraint(
            "dedup_reason IS NULL OR dedup_reason IN "
            "('exact_hash', 'canonical_url', 'near_duplicate')",
            name="ck_sources_dedup_reason",
        ),
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
    #: sha256 of the normalised content. Unique *per source*: re-ingesting the
    #: same content for the same source is a no-op, while two runs - or two
    #: users - holding the same document each keep their own row. A global key
    #: would force them to share one, and ``evidence.document_id`` cascades, so
    #: one user deleting a run would delete another user's evidence (ADR 0012).
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Format-specific facts: page count, the PDF producer, how the language was
    #: decided, which chunker produced the chunks. Descriptive, never a filter
    #: key - filters read the chunk metadata, which is indexed.
    doc_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    source: Mapped[SourceRow] = relationship(back_populates="documents")
    chunks: Mapped[list[DocumentChunkRow]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        Index(
            "uq_documents_source_id_content_hash",
            "source_id",
            "content_hash",
            unique=True,
        ),
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
    #:
    #: Deferred: loading a chunk does not load its vector. Only vector search
    #: reads it, and it selects the column explicitly; everything else - listing
    #: and filtering chunks - would otherwise pull three kilobytes a row it never
    #: looks at. Written with an explicit cast rather than through this mapping
    #: (see the documents repository).
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSIONS), deferred=True
    )
    #: Set in the same statement as ``embedding``, so it doubles as the
    #: "is this chunk embedded" marker - one that needs no pgvector to read.
    embedding_model: Mapped[str | None] = mapped_column(String(120))
    #: Generated by Postgres from `content`, so it can never drift out of sync
    #: with the text it indexes.
    tsv: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed(f"to_tsvector('{TEXT_SEARCH_CONFIG}', content)", persisted=True)
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
