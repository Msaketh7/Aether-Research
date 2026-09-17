"""Source DTOs (FR-5).

Every field the citation validator needs in order to prove a source was really
retrieved is present here - in particular `accessed_at` and `content_hash`,
which are what distinguish a citation from an assertion.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.core.enums import SourceType
from app.research.schemas import ApiModel


class SourceCredibility(ApiModel):
    """Reputation of the *origin*, not a quality judgement of the content."""

    is_primary: bool
    tier: Literal["official", "reputable", "community", "unknown"]
    domain_reputation: float = Field(ge=0.0, le=1.0)
    notes: str | None = None


class Source(ApiModel):
    id: UUID
    run_id: UUID
    url: str
    canonical_url: str
    domain: str
    source_type: SourceType
    title: str
    publisher: str
    author: str | None
    published_at: datetime | None
    accessed_at: datetime
    content_hash: str
    credibility_score: float = Field(ge=0.0, le=1.0)
    credibility_metadata: SourceCredibility
    dedup_cluster_id: UUID | None
    #: ``None`` means relevance was never measured for this source. Distinct from
    #: a low score, and the reason the column is nullable: a placeholder here is
    #: read by a person as a number the system produced (migration 0007).
    relevance_score: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    task_external_id: str | None
    claim_count: int
    excerpt: str


class SourceCluster(ApiModel):
    """Near-duplicates, grouped so the same content is not double-counted as
    corroboration."""

    cluster_id: UUID
    primary_source_id: UUID
    duplicate_source_ids: list[UUID]
    reason: Literal["exact_hash", "canonical_url", "near_duplicate"]


class SourcesResponse(ApiModel):
    sources: list[Source]
    clusters: list[SourceCluster]
    next_cursor: str | None = None
    total: int
