import type { IsoDateTime, Uuid } from './common';
import type { EvaluationKind, FeedbackCategory } from './enums';

/**
 * Evaluation results (Phase 18).
 *
 * Every value here is produced by an executed benchmark. The UI renders
 * `null` as "not yet measured" and never substitutes a zero, because a zero
 * and an unmeasured metric mean very different things.
 */

export interface MetricValue {
  key: string;
  label: string;
  value: number | null;
  /** `ratio` renders as a percentage, `seconds`/`usd`/`count` render literally. */
  unit: 'ratio' | 'seconds' | 'usd' | 'count';
  /** Gate value from configuration, or null when this metric is not gated. */
  threshold: number | null;
  /** `null` when either side is unmeasured. */
  passed: boolean | null;
  /** Change against the previous benchmark run, or null when there is none. */
  delta: number | null;
}

export interface EvaluationCaseResult {
  case_id: string;
  question: string;
  kind: EvaluationKind;
  passed: boolean;
  metrics: MetricValue[];
  /** Why it failed, in plain language. Empty when it passed. */
  failures: string[];
  run_id: Uuid | null;
  duration_seconds: number;
}

export interface EvaluationRun {
  id: Uuid;
  dataset_version: string;
  git_sha: string;
  kind: EvaluationKind;
  /** The model routing in force, so results are attributable. */
  model_config: Record<string, string>;
  started_at: IsoDateTime;
  completed_at: IsoDateTime | null;
  passed: boolean;
  case_count: number;
  passed_count: number;
  metrics: MetricValue[];
}

export interface EvaluationsResponse {
  /** Null when no benchmark has ever run. The UI must handle this explicitly. */
  latest: EvaluationRun | null;
  history: EvaluationRun[];
  cases: EvaluationCaseResult[];
}

/** System health panel on /evaluations. Values come from live telemetry. */
export interface SystemMetrics {
  window: '1h' | '24h' | '7d';
  research_success_rate: number | null;
  research_failure_rate: number | null;
  latency_p50_seconds: number | null;
  latency_p95_seconds: number | null;
  latency_p99_seconds: number | null;
  llm_latency_p95_ms: number | null;
  tool_latency_p95_ms: number | null;
  cache_hit_rate: number | null;
  total_tokens: number | null;
  total_cost_usd: number | null;
  queue_depth: number | null;
  active_workers: number | null;
}

export interface FeedbackRequest {
  run_id: Uuid;
  report_id: Uuid;
  rating: 1 | 2 | 3 | 4 | 5;
  helpful: boolean;
  category: FeedbackCategory;
  comment?: string;
}
