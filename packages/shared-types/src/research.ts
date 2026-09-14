import type { IsoDate, IsoDateTime, UnitInterval, Uuid } from './common';
import type { ResearchMode, RunStatus, TaskPriority, TaskStatus } from './enums';

/**
 * What the user submits on /research/new (FR-2).
 * The API validates this again; client-side validation is convenience only.
 */
export interface CreateResearchRequest {
  question: string;
  mode: ResearchMode;
  /** Required for `deep`; ignored for `quick`. Bounded by the FR-8 limits. */
  depth?: number;
  /** Optional domain hints, e.g. `nvidia.com`, `sec.gov`. */
  domains?: string[];
  date_range_start?: IsoDate | null;
  date_range_end?: IsoDate | null;
  /** Ids of documents already uploaded via POST /files. At most 10. */
  document_ids?: Uuid[];
  /** Set for follow-up runs; the planner is seeded with this run's evidence. */
  parent_run_id?: Uuid | null;
}

/** `202 Accepted` body. The work has been queued, not performed. */
export interface CreateResearchResponse {
  run_id: Uuid;
  status: RunStatus;
  /** Where to subscribe for progress. */
  events_url: string;
}

/** Hard ceilings applied to a run (FR-8). Echoed so the UI can show headroom. */
export interface RunLimits {
  max_iterations: number;
  max_sources: number;
  max_search_queries: number;
  max_runtime_seconds: number;
  max_cost_usd: number;
}

/** Live consumption against {@link RunLimits}. */
export interface RunUsage {
  iterations: number;
  sources: number;
  elapsed_seconds: number;
  total_tokens: number;
  cost_usd: number;
}

/** One planner subtask. */
export interface ResearchTask {
  id: Uuid;
  run_id: Uuid;
  /** Stable slug from the planner, e.g. `market`, `competitors`. */
  external_id: string;
  question: string;
  priority: TaskPriority;
  status: TaskStatus;
  /** Why the planner created this subtask. */
  rationale: string;
  /** Which loop iteration produced it. */
  iteration: number;
  source_count: number;
  claim_count: number;
  completed_at: IsoDateTime | null;
}

/** The planner's output for a run (FR-3). */
export interface ResearchPlan {
  research_goal: string;
  tasks: ResearchTask[];
  /** Bumped every time the critic sends work back. */
  iteration: number;
}

/** One execution of a research question. The central object of the product. */
export interface ResearchRun {
  id: Uuid;
  user_id: Uuid;
  parent_run_id: Uuid | null;
  title: string;
  question: string;
  mode: ResearchMode;
  depth: number;
  domains: string[];
  date_range_start: IsoDate | null;
  date_range_end: IsoDate | null;
  status: RunStatus;
  /** 0..1, derived from completed subtasks; drives the progress bar. */
  progress: UnitInterval;
  limits: RunLimits;
  usage: RunUsage;
  source_count: number;
  claim_count: number;
  contradiction_count: number;
  /** Set when a hard limit truncated the run; rendered as a banner, not hidden. */
  coverage_caveat: string | null;
  has_report: boolean;
  created_at: IsoDateTime;
  started_at: IsoDateTime | null;
  completed_at: IsoDateTime | null;
  error: { code: string; message: string } | null;
}

/** Compact row for the dashboard and history list. */
export interface ResearchRunSummary {
  id: Uuid;
  title: string;
  question: string;
  mode: ResearchMode;
  status: RunStatus;
  progress: UnitInterval;
  source_count: number;
  claim_count: number;
  contradiction_count: number;
  cost_usd: number;
  created_at: IsoDateTime;
  completed_at: IsoDateTime | null;
}

/** Aggregates for the dashboard header. All values are measured, never estimated. */
export interface DashboardStats {
  total_runs: number;
  completed_runs: number;
  running_runs: number;
  failed_runs: number;
  total_sources: number;
  total_claims: number;
  total_cost_usd: number;
  /** Median wall-clock seconds for completed runs, or null with too few samples. */
  median_runtime_seconds: number | null;
}
