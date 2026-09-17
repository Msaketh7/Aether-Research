import type { IsoDateTime, UnitInterval, Uuid } from './common';
import type { SourceType } from './enums';

/**
 * A discovered document reference (FR-5). Every field the citation validator
 * needs to prove a source was really retrieved is present here.
 */
export interface Source {
  id: Uuid;
  run_id: Uuid;
  url: string;
  /** Post-canonicalization URL; two sources sharing this are the same page. */
  canonical_url: string;
  domain: string;
  source_type: SourceType;
  title: string;
  publisher: string;
  author: string | null;
  published_at: IsoDateTime | null;
  /** When Aether fetched it. Distinct from `published_at` and never inferred. */
  accessed_at: IsoDateTime;
  /** sha256 of the normalized content; the exact-duplicate key. */
  content_hash: string;
  /** 0..1 domain-and-type reputation, not a quality judgement of the content. */
  credibility_score: UnitInterval;
  credibility_metadata: SourceCredibility;
  /** Groups near-duplicates so the UI can collapse them. */
  dedup_cluster_id: Uuid | null;
  /**
   * How well this source matched the subtask that found it, or `null` when that
   * was never measured. Nullable rather than defaulted: a placeholder here is
   * read by a person as a score the system produced.
   */
  relevance_score: UnitInterval | null;
  /** Which planner subtask surfaced it. */
  task_external_id: string | null;
  claim_count: number;
  /** Short extract shown on the sources page; never the full document. */
  excerpt: string;
}

export interface SourceCredibility {
  /** A filing or a company statement is primary; commentary about it is not. */
  is_primary: boolean;
  /** Coarse tier used in ranking: `official`, `reputable`, `community`, `unknown`. */
  tier: 'official' | 'reputable' | 'community' | 'unknown';
  domain_reputation: UnitInterval;
  /** Human-readable notes surfaced in the UI tooltip. */
  notes: string | null;
}

/** Near-duplicate grouping, so the UI shows one row with an "n duplicates" badge. */
export interface SourceCluster {
  cluster_id: Uuid;
  /** The source chosen as canonical for the cluster. */
  primary_source_id: Uuid;
  duplicate_source_ids: Uuid[];
  reason: 'exact_hash' | 'canonical_url' | 'near_duplicate';
}

export interface SourcesResponse {
  sources: Source[];
  clusters: SourceCluster[];
  next_cursor: string | null;
  total: number;
}
