import type { IsoDateTime, UnitInterval, Uuid } from './common';
import type { AgentName, RunStatus, SourceType, TaskPriority } from './enums';

/**
 * Server-Sent Event contract (ADR 0006).
 *
 * Every event is persisted server-side as well as streamed, so the activity
 * trace is reconstructible for a user who was not watching. `seq` is monotonic
 * per run: a client that reconnects sends `Last-Event-ID` and the API replays
 * from a bounded buffer, so no event is silently lost.
 */
export const RESEARCH_EVENT_TYPES = [
  'research_started',
  'planner_started',
  'planner_completed',
  'subtask_started',
  'search_started',
  'source_found',
  'source_processed',
  'sources_progress',
  'claim_extracted',
  'evidence_progress',
  'verification_started',
  'contradiction_found',
  'critic_started',
  'additional_research_requested',
  'iteration_started',
  'answer_started',
  'answer_delta',
  'answer_completed',
  'synthesis_started',
  'citation_check',
  'report_completed',
  'research_failed',
  'research_cancelled',
] as const;
export type ResearchEventType = (typeof RESEARCH_EVENT_TYPES)[number];

interface EventBase<T extends ResearchEventType, P> {
  /** Monotonic per run. Used for ordering and for `Last-Event-ID` replay. */
  seq: number;
  type: T;
  run_id: Uuid;
  at: IsoDateTime;
  /** Run status after this event, so the UI never needs a separate poll. */
  status: RunStatus;
  payload: P;
}

export type ResearchEvent =
  | EventBase<'research_started', { question: string; mode: string }>
  | EventBase<'planner_started', { iteration: number }>
  | EventBase<
      'planner_completed',
      {
        research_goal: string;
        iteration: number;
        tasks: Array<{
          external_id: string;
          question: string;
          priority: TaskPriority;
          rationale: string;
        }>;
      }
    >
  | EventBase<'subtask_started', { task_external_id: string; question: string }>
  | EventBase<'search_started', { task_external_id: string; query: string; provider: string }>
  | EventBase<
      'source_found',
      {
        source_id: Uuid;
        title: string;
        url: string;
        publisher: string;
        source_type: SourceType;
        /** `null` when relevance was not measured, as on `Source`. */
        relevance_score: UnitInterval | null;
        task_external_id: string | null;
      }
    >
  | EventBase<
      'source_processed',
      { source_id: Uuid; title: string; chunk_count: number; duplicate_of: Uuid | null }
    >
  | EventBase<'sources_progress', { processed: number; discovered: number; limit: number }>
  | EventBase<
      'claim_extracted',
      {
        claim_id: Uuid;
        text: string;
        confidence: UnitInterval;
        source_id: Uuid;
        task_external_id: string | null;
      }
    >
  | EventBase<'evidence_progress', { claims: number; evidence: number; verified: number }>
  | EventBase<'verification_started', { claim_count: number }>
  | EventBase<
      'contradiction_found',
      {
        contradiction_id: Uuid;
        normalized_key: string;
        value_a: string;
        value_b: string;
        likely_reason: string;
      }
    >
  | EventBase<'critic_started', { iteration: number }>
  | EventBase<
      'additional_research_requested',
      { iteration: number; reason: string; new_task_count: number }
    >
  | EventBase<'iteration_started', { iteration: number; max_iterations: number }>
  /**
   * A new answer is about to be written. A client resets whatever it has
   * accumulated: a worker that crashed halfway through an answer leaves a
   * partial one on screen, and the next attempt writes a different answer that
   * must replace it rather than continue it.
   */
  | EventBase<'answer_started', Record<string, never>>
  /**
   * One piece of the answer, to append. `index` is 1-based within the current
   * answer and resets on `answer_started`; pieces are phrase-sized rather than
   * per-token because each one is a stored row.
   */
  | EventBase<'answer_delta', { index: number; text: string }>
  /**
   * The finished answer, in full. Repeats every character already streamed on
   * purpose: it is what a reader who was not watching is replayed, and what a
   * client reconciles its accumulated text against so a dropped piece heals.
   */
  | EventBase<
      'answer_completed',
      {
        text: string;
        model: string;
        word_count: number;
        citation_count: number;
        /** The model reached its output ceiling, so the answer stops early. */
        truncated: boolean;
      }
    >
  | EventBase<'synthesis_started', { section_count: number }>
  | EventBase<'citation_check', { checked: number; valid: number; rejected: number }>
  | EventBase<
      'report_completed',
      {
        report_id: Uuid;
        word_count: number;
        /** `null` when the report cites no claim, as on `Report`. */
        overall_confidence: UnitInterval | null;
        cost_usd: number;
        coverage_caveat: string | null;
      }
    >
  | EventBase<'research_failed', { code: string; message: string; partial_report: boolean }>
  | EventBase<'research_cancelled', { cancelled_by: string }>;

/** Narrowing helper for reducers that switch on `type`. */
export type ResearchEventOf<T extends ResearchEventType> = Extract<ResearchEvent, { type: T }>;

/**
 * Coarse workflow stages the activity page renders as a checklist. Derived from
 * events rather than sent, so the checklist cannot disagree with the trace.
 */
export const RESEARCH_STAGES = [
  'planning',
  'searching',
  'reading',
  'extracting',
  'verifying',
  'contradictions',
  'answering',
  'writing',
] as const;
export type ResearchStage = (typeof RESEARCH_STAGES)[number];

export type StageState = 'pending' | 'active' | 'done' | 'failed';

export interface StageProgress {
  stage: ResearchStage;
  state: StageState;
  /** e.g. "14 sources" - always a measured count, never a guess. */
  detail: string | null;
  agent: AgentName | null;
}
