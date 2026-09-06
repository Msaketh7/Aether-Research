import type { IsoDateTime, UnitInterval, Uuid } from './common';
import type { ClaimStatus, ClaimType, ContradictionResolution, EvidenceStance } from './enums';

/**
 * The exact text that supports or refutes a claim (FR-6).
 *
 * `span_text` is verbatim and its offsets point into the stored normalized
 * document, which is what makes a citation checkable rather than assertable.
 */
export interface Evidence {
  id: Uuid;
  claim_id: Uuid;
  source_id: Uuid;
  document_id: Uuid;
  span_text: string;
  span_start: number;
  span_end: number;
  stance: EvidenceStance;
  /** Which agent produced this span, for trace and for evaluation attribution. */
  extractor_agent: string;
  extractor_model: string;
  confidence: UnitInterval;
  created_at: IsoDateTime;
}

/** A single normalized assertion. */
export interface Claim {
  id: Uuid;
  run_id: Uuid;
  task_id: Uuid | null;
  task_external_id: string | null;
  text: string;
  subject: string;
  predicate: string;
  object_value: string;
  claim_type: ClaimType;
  /** Groups the same assertion across sources; drives contradiction detection. */
  normalized_key: string;
  confidence: UnitInterval;
  status: ClaimStatus;
  /** How many independent sources back it. 1 means uncorroborated. */
  corroboration_count: number;
  first_seen_at: IsoDateTime;
}

/** A claim with its evidence attached, as the evidence page renders it. */
export interface ClaimWithEvidence extends Claim {
  supporting: Evidence[];
  refuting: Evidence[];
}

/**
 * A genuine disagreement between sources (FR-7).
 * Never resolved silently: an unresolved contradiction is a first-class result.
 */
export interface Contradiction {
  id: Uuid;
  run_id: Uuid;
  normalized_key: string;
  claim_a_id: Uuid;
  claim_b_id: Uuid;
  /** Denormalized for display so the UI needs no second fetch. */
  claim_a_text: string;
  claim_b_text: string;
  value_a: string;
  value_b: string;
  source_a_id: Uuid;
  source_b_id: Uuid;
  /** The system's best guess, e.g. "different fiscal periods". Always shown as a guess. */
  likely_reason: string;
  resolution: ContradictionResolution;
  resolved_by: string | null;
  detected_at: IsoDateTime;
}

export interface EvidenceResponse {
  claims: ClaimWithEvidence[];
  contradictions: Contradiction[];
  next_cursor: string | null;
  total: number;
}
