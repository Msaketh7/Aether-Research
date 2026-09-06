"""Claim, evidence and contradiction DTOs (FR-6, FR-7)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.core.enums import ClaimStatus, ClaimType, ContradictionResolution, EvidenceStance
from app.research.schemas import ApiModel


class Evidence(ApiModel):
    """The exact text supporting or refuting a claim.

    `span_text` is verbatim and the offsets point into the stored normalised
    document. That is what makes a citation checkable rather than assertable.
    """

    id: UUID
    claim_id: UUID
    source_id: UUID
    document_id: UUID
    span_text: str
    span_start: int
    span_end: int
    stance: EvidenceStance
    extractor_agent: str
    extractor_model: str
    confidence: float = Field(ge=0.0, le=1.0)
    created_at: datetime


class Claim(ApiModel):
    id: UUID
    run_id: UUID
    task_id: UUID | None
    task_external_id: str | None
    text: str
    subject: str
    predicate: str
    object_value: str
    claim_type: ClaimType
    normalized_key: str
    confidence: float = Field(ge=0.0, le=1.0)
    status: ClaimStatus
    corroboration_count: int
    first_seen_at: datetime


class ClaimWithEvidence(Claim):
    supporting: list[Evidence] = Field(default_factory=list)
    refuting: list[Evidence] = Field(default_factory=list)


class Contradiction(ApiModel):
    """A genuine disagreement between sources.

    Never resolved silently: an unresolved contradiction is a first-class
    result, and `likely_reason` is explicitly a hypothesis.
    """

    id: UUID
    run_id: UUID
    normalized_key: str
    claim_a_id: UUID
    claim_b_id: UUID
    claim_a_text: str
    claim_b_text: str
    value_a: str
    value_b: str
    source_a_id: UUID
    source_b_id: UUID
    likely_reason: str
    resolution: ContradictionResolution
    resolved_by: str | None
    detected_at: datetime


class EvidenceResponse(ApiModel):
    claims: list[ClaimWithEvidence]
    contradictions: list[Contradiction]
    next_cursor: str | None = None
    total: int
