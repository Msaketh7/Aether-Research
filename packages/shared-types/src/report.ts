import type { IsoDateTime, UnitInterval, Uuid } from './common';
import type { ReportSectionKind, ReportStatus } from './enums';

/**
 * The link behind every `[n]` marker in the report.
 *
 * A citation is only emitted once the validator has confirmed the whole chain
 * citation -> claim -> evidence -> document -> source resolves. `quote` is the
 * verbatim evidence span, so the UI can show proof without another request.
 */
export interface Citation {
  id: Uuid;
  /** The `[n]` number, unique and stable within one report. */
  ordinal: number;
  report_section_id: Uuid;
  claim_id: Uuid;
  source_id: Uuid;
  source_title: string;
  source_url: string;
  source_publisher: string;
  quote: string;
  confidence: UnitInterval;
}

export interface ReportSection {
  id: Uuid;
  report_id: Uuid;
  kind: ReportSectionKind;
  heading: string;
  ordinal: number;
  /** Markdown containing inline `[n]` markers that resolve against `citations`. */
  content_md: string;
}

export interface Report {
  id: Uuid;
  run_id: Uuid;
  title: string;
  summary: string;
  /** `null` when the report cites no claim, so there was nothing to average. */
  overall_confidence: UnitInterval | null;
  status: ReportStatus;
  /** The synthesizer model, recorded so a quality change is attributable. */
  model: string;
  word_count: number;
  generated_at: IsoDateTime;
  /** Null until the citation validator has passed over the report. */
  validated_at: IsoDateTime | null;
  /** Present when a hard limit truncated the research (FR-8). */
  coverage_caveat: string | null;
}

/** Outcome of the citation validation pass (FR-12). */
export interface CitationValidationSummary {
  checked: number;
  valid: number;
  /** Citations dropped because the evidence chain did not resolve. */
  rejected: number;
  rejection_reasons: Array<{ reason: string; count: number }>;
  validated_at: IsoDateTime;
}

/** Everything /research/[id]/report needs in one response. */
export interface ReportResponse {
  report: Report;
  sections: ReportSection[];
  citations: Citation[];
  validation: CitationValidationSummary | null;
}
