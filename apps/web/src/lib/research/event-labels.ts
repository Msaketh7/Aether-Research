import type { ResearchEvent, ResearchEventType } from '@aether/shared-types';

import { formatPercent } from '@/lib/format';

/**
 * Turns a raw event into the line the activity feed shows.
 *
 * Kept out of the components so the wording is testable and consistent, and so
 * a new event type is a compile error here rather than a silently blank row.
 */

export type EventTone = 'neutral' | 'progress' | 'positive' | 'warning' | 'danger';

export interface EventDescription {
  title: string;
  detail: string | null;
  tone: EventTone;
  /** Optional link target, e.g. the source a `source_found` event refers to. */
  href: string | null;
}

const TONES: Record<ResearchEventType, EventTone> = {
  research_started: 'neutral',
  planner_started: 'progress',
  planner_completed: 'positive',
  subtask_started: 'progress',
  search_started: 'progress',
  source_found: 'neutral',
  source_processed: 'neutral',
  sources_progress: 'progress',
  claim_extracted: 'neutral',
  evidence_progress: 'progress',
  verification_started: 'progress',
  contradiction_found: 'warning',
  critic_started: 'progress',
  additional_research_requested: 'warning',
  iteration_started: 'progress',
  synthesis_started: 'progress',
  citation_check: 'progress',
  report_completed: 'positive',
  research_failed: 'danger',
  research_cancelled: 'warning',
};

export function describeEvent(event: ResearchEvent): EventDescription {
  const tone = TONES[event.type];

  switch (event.type) {
    case 'research_started':
      return { title: 'Research started', detail: event.payload.question, tone, href: null };

    case 'planner_started':
      return {
        title: 'Planner running',
        detail: `Decomposing the question (iteration ${event.payload.iteration})`,
        tone,
        href: null,
      };

    case 'planner_completed':
      return {
        title: `Plan ready — ${event.payload.tasks.length} subtasks`,
        detail: event.payload.tasks.map((t) => t.external_id).join(', '),
        tone,
        href: null,
      };

    case 'subtask_started':
      return {
        title: `Researching: ${event.payload.task_external_id}`,
        detail: event.payload.question,
        tone,
        href: null,
      };

    case 'search_started':
      return {
        title: 'Search',
        detail: `"${event.payload.query}" via ${event.payload.provider}`,
        tone,
        href: null,
      };

    case 'source_found':
      return {
        title: event.payload.title,
        detail: `${event.payload.publisher} · relevance ${formatPercent(event.payload.relevance_score)}`,
        tone,
        href: event.payload.url,
      };

    case 'source_processed':
      return {
        title: event.payload.duplicate_of
          ? `Duplicate skipped: ${event.payload.title}`
          : `Read: ${event.payload.title}`,
        detail: event.payload.duplicate_of
          ? 'Already covered by an earlier source'
          : `${event.payload.chunk_count} chunks indexed`,
        tone: event.payload.duplicate_of ? 'warning' : tone,
        href: null,
      };

    case 'sources_progress':
      return {
        title: `Processed ${event.payload.processed} of ${event.payload.discovered} sources`,
        detail: `Source ceiling ${event.payload.limit}`,
        tone,
        href: null,
      };

    case 'claim_extracted':
      return {
        title: event.payload.text,
        detail: `confidence ${(event.payload.confidence * 100).toFixed(0)}%`,
        tone,
        href: null,
      };

    case 'evidence_progress':
      return {
        title: `${event.payload.claims} claims, ${event.payload.evidence} evidence spans`,
        detail: `${event.payload.verified} verified`,
        tone,
        href: null,
      };

    case 'verification_started':
      return {
        title: 'Verifying claims',
        detail: `${event.payload.claim_count} claims to corroborate`,
        tone,
        href: null,
      };

    case 'contradiction_found':
      return {
        title: `Contradiction: ${event.payload.value_a} vs ${event.payload.value_b}`,
        detail: event.payload.likely_reason,
        tone,
        href: null,
      };

    case 'critic_started':
      return {
        title: 'Critic reviewing coverage',
        detail: `Iteration ${event.payload.iteration}`,
        tone,
        href: null,
      };

    case 'additional_research_requested':
      return {
        title: `More research needed — ${event.payload.new_task_count} new subtasks`,
        detail: event.payload.reason,
        tone,
        href: null,
      };

    case 'iteration_started':
      return {
        title: `Iteration ${event.payload.iteration} of ${event.payload.max_iterations}`,
        detail: null,
        tone,
        href: null,
      };

    case 'synthesis_started':
      return {
        title: 'Writing report',
        detail: `${event.payload.section_count} sections`,
        tone,
        href: null,
      };

    case 'citation_check':
      return {
        title: `Citation validation — ${event.payload.valid}/${event.payload.checked} valid`,
        detail:
          event.payload.rejected > 0
            ? `${event.payload.rejected} rejected as unverifiable`
            : 'All citations resolved',
        tone: event.payload.rejected > 0 ? 'warning' : tone,
        href: null,
      };

    case 'report_completed':
      return {
        title: 'Report complete',
        detail: `${event.payload.word_count} words · confidence ${formatPercent(event.payload.overall_confidence)}`,
        tone,
        href: null,
      };

    case 'research_failed':
      return { title: 'Research failed', detail: event.payload.message, tone, href: null };

    case 'research_cancelled':
      return {
        title: 'Cancelled',
        detail: `Stopped by ${event.payload.cancelled_by}`,
        tone,
        href: null,
      };
  }
}
