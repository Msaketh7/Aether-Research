import {
  RESEARCH_STAGES,
  type AgentName,
  type ResearchEvent,
  type ResearchEventType,
  type ResearchStage,
  type RunStatus,
  type StageProgress,
  type StageState,
} from '@aether/shared-types';

/**
 * Derives the activity checklist from the event stream.
 *
 * The checklist is computed, never transmitted. If it were its own event type
 * the server could tell the user "verifying claims" while the trace showed
 * something else; deriving it from the same events the trace renders makes that
 * disagreement impossible.
 *
 * Pure function, no React: it is unit-tested directly.
 */

/** Which checklist row each event belongs to. `null` = not a stage signal. */
const EVENT_STAGE: Record<ResearchEventType, ResearchStage | null> = {
  research_started: null,
  planner_started: 'planning',
  planner_completed: 'planning',
  subtask_started: 'searching',
  search_started: 'searching',
  source_found: 'searching',
  source_processed: 'reading',
  sources_progress: 'reading',
  claim_extracted: 'extracting',
  evidence_progress: 'extracting',
  verification_started: 'verifying',
  contradiction_found: 'contradictions',
  critic_started: 'contradictions',
  additional_research_requested: 'searching',
  iteration_started: 'planning',
  answer_started: 'answering',
  // Not a stage signal. A piece of the answer arriving says nothing the
  // `answer_started` before it did not already say, and a stage row that
  // re-derives itself forty times a second is a stage row that flickers.
  answer_delta: null,
  answer_completed: 'answering',
  synthesis_started: 'writing',
  citation_check: 'writing',
  report_completed: 'writing',
  research_failed: null,
  research_cancelled: null,
};

const STAGE_AGENT: Record<ResearchStage, AgentName> = {
  planning: 'planner',
  searching: 'researcher',
  reading: 'researcher',
  extracting: 'evidence_extractor',
  verifying: 'verifier',
  contradictions: 'critic',
  answering: 'answerer',
  writing: 'synthesizer',
};

export const STAGE_LABELS: Record<ResearchStage, string> = {
  planning: 'Planning',
  searching: 'Searching',
  reading: 'Reading sources',
  extracting: 'Extracting evidence',
  verifying: 'Verifying claims',
  contradictions: 'Detecting contradictions',
  answering: 'Answering',
  writing: 'Writing report',
};

/** Counters accumulated from the stream, used for the per-stage detail text. */
export interface EventTotals {
  queries: number;
  sourcesFound: number;
  sourcesProcessed: number;
  sourcesDiscovered: number;
  sourceLimit: number | null;
  claims: number;
  evidence: number;
  verified: number;
  contradictions: number;
  citationsChecked: number;
  citationsRejected: number;
  iteration: number;
  wordCount: number | null;
  /** Words in the direct answer, once it is finished. `null` = not written. */
  answerWords: number | null;
  subtasks: number;
  lastSeq: number;
}

export const EMPTY_TOTALS: EventTotals = {
  queries: 0,
  sourcesFound: 0,
  sourcesProcessed: 0,
  sourcesDiscovered: 0,
  sourceLimit: null,
  claims: 0,
  evidence: 0,
  verified: 0,
  contradictions: 0,
  citationsChecked: 0,
  citationsRejected: 0,
  iteration: 0,
  wordCount: null,
  answerWords: null,
  subtasks: 0,
  lastSeq: 0,
};

export function accumulate(events: readonly ResearchEvent[]): EventTotals {
  const totals: EventTotals = { ...EMPTY_TOTALS };

  for (const event of events) {
    totals.lastSeq = Math.max(totals.lastSeq, event.seq);

    switch (event.type) {
      case 'planner_completed':
        totals.subtasks = event.payload.tasks.length;
        totals.iteration = Math.max(totals.iteration, event.payload.iteration);
        break;
      case 'search_started':
        totals.queries += 1;
        break;
      case 'source_found':
        totals.sourcesFound += 1;
        break;
      case 'source_processed':
        totals.sourcesProcessed += 1;
        break;
      case 'sources_progress':
        totals.sourcesProcessed = Math.max(totals.sourcesProcessed, event.payload.processed);
        totals.sourcesDiscovered = Math.max(totals.sourcesDiscovered, event.payload.discovered);
        totals.sourceLimit = event.payload.limit;
        break;
      case 'claim_extracted':
        totals.claims += 1;
        break;
      case 'evidence_progress':
        totals.claims = Math.max(totals.claims, event.payload.claims);
        totals.evidence = Math.max(totals.evidence, event.payload.evidence);
        totals.verified = Math.max(totals.verified, event.payload.verified);
        break;
      case 'contradiction_found':
        totals.contradictions += 1;
        break;
      case 'iteration_started':
        totals.iteration = Math.max(totals.iteration, event.payload.iteration);
        break;
      case 'citation_check':
        totals.citationsChecked = event.payload.checked;
        totals.citationsRejected = event.payload.rejected;
        break;
      case 'answer_completed':
        totals.answerWords = event.payload.word_count;
        break;
      case 'report_completed':
        totals.wordCount = event.payload.word_count;
        break;
      default:
        break;
    }
  }

  totals.sourcesDiscovered = Math.max(totals.sourcesDiscovered, totals.sourcesFound);
  return totals;
}

function detailFor(stage: ResearchStage, totals: EventTotals, state: StageState): string | null {
  if (state === 'pending') return null;
  switch (stage) {
    case 'planning':
      return totals.subtasks > 0 ? `${totals.subtasks} subtasks` : null;
    case 'searching':
      return totals.queries > 0 ? `${totals.queries} queries` : null;
    case 'reading':
      if (totals.sourcesDiscovered === 0) return null;
      return `${totals.sourcesProcessed}/${totals.sourcesDiscovered} sources`;
    case 'extracting':
      return totals.claims > 0 ? `${totals.claims} claims` : null;
    case 'verifying':
      return totals.verified > 0 ? `${totals.verified} verified` : null;
    case 'contradictions':
      // Only report a count we actually observed. A finished run replayed
      // without its event stream has no counters, and rendering "0 found"
      // there would contradict the run's own contradiction total.
      return totals.contradictions > 0 ? `${totals.contradictions} found` : null;
    case 'answering':
      return totals.answerWords !== null ? `${totals.answerWords} words` : null;
    case 'writing':
      if (totals.wordCount !== null) return `${totals.wordCount} words`;
      if (totals.citationsChecked > 0) return `${totals.citationsChecked} citations checked`;
      return null;
    default:
      return null;
  }
}

const STAGE_ORDER = new Map<ResearchStage, number>(
  RESEARCH_STAGES.map((stage, index) => [stage, index]),
);

/**
 * @param events every event received so far, in any order
 * @param status the run's status, which decides how the furthest stage renders
 *   once the stream has ended
 */
export function deriveStages(events: readonly ResearchEvent[], status: RunStatus): StageProgress[] {
  const totals = accumulate(events);

  // The furthest stage the run has demonstrably reached.
  let furthest = -1;
  for (const event of events) {
    const stage = EVENT_STAGE[event.type];
    if (!stage) continue;
    furthest = Math.max(furthest, STAGE_ORDER.get(stage) ?? -1);
  }

  const failed = status === 'failed';
  const cancelled = status === 'cancelled';
  const completed = status === 'completed';

  return RESEARCH_STAGES.map((stage, index): StageProgress => {
    let state: StageState;
    if (completed) {
      state = 'done';
    } else if (index < furthest) {
      state = 'done';
    } else if (index === furthest) {
      // Terminal but not completed: the stage we stopped in is the one that
      // failed or was interrupted. Anything after it never started.
      if (failed) state = 'failed';
      else if (cancelled) state = 'pending';
      else state = 'active';
    } else {
      state = 'pending';
    }

    return {
      stage,
      state,
      detail: detailFor(stage, totals, state),
      agent: STAGE_AGENT[stage],
    };
  });
}

/** Fraction of the checklist completed, for the run progress bar. */
export function stageProgressRatio(stages: readonly StageProgress[]): number {
  if (stages.length === 0) return 0;
  const done = stages.filter((s) => s.state === 'done').length;
  const active = stages.some((s) => s.state === 'active') ? 0.5 : 0;
  return Math.min(1, (done + active) / stages.length);
}
