import {
  RESEARCH_STAGES,
  type ResearchEvent,
  type ResearchEventType,
  type RunStatus,
} from '@aether/shared-types';
import { describe, expect, it } from 'vitest';
import { accumulate, deriveStages, stageProgressRatio } from './stages';

/**
 * The checklist is derived, not transmitted (see stages.ts). These tests pin
 * the derivation, because a wrong checklist would tell the user the run is
 * doing something the trace says it is not.
 */

let seq = 0;

function event<T extends ResearchEventType>(
  type: T,
  status: RunStatus,
  payload: unknown = {},
): ResearchEvent {
  seq += 1;
  return {
    seq,
    type,
    run_id: 'run-1',
    at: new Date(1_700_000_000_000 + seq * 1000).toISOString(),
    status,
    payload,
  } as ResearchEvent;
}

function stageState(stages: ReturnType<typeof deriveStages>, name: string) {
  return stages.find((stage) => stage.stage === name)?.state;
}

describe('deriveStages', () => {
  it('marks everything pending before any event arrives', () => {
    const stages = deriveStages([], 'queued');
    // One row per declared stage. Asserted against the vocabulary rather than a
    // literal, so adding a stage is a change to the contract and not a test to
    // renumber.
    expect(stages).toHaveLength(RESEARCH_STAGES.length);
    expect(stages.map((stage) => stage.stage)).toEqual([...RESEARCH_STAGES]);
    expect(stages.every((stage) => stage.state === 'pending')).toBe(true);
  });

  it('reaches the answering stage before the writing stage', () => {
    const stages = deriveStages(
      [
        event('answer_started', 'synthesizing'),
        event('answer_completed', 'synthesizing', {
          text: 'An answer [1].',
          model: 'scripted',
          word_count: 3,
          citation_count: 1,
          truncated: false,
        }),
      ],
      'synthesizing',
    );

    // The answer is the furthest the run has demonstrably got. Writing the
    // report comes after it, and claiming otherwise would tell a reader the
    // report exists while they are still watching the answer arrive.
    expect(stageState(stages, 'answering')).toBe('active');
    expect(stageState(stages, 'writing')).toBe('pending');
    expect(stages.find((stage) => stage.stage === 'answering')?.detail).toBe('3 words');
  });

  it('marks earlier stages done and the current stage active', () => {
    const stages = deriveStages(
      [
        event('research_started', 'planning', { question: 'q', mode: 'deep' }),
        event('planner_completed', 'researching', {
          research_goal: 'g',
          iteration: 1,
          tasks: [{ external_id: 'market', question: 'q', priority: 'high', rationale: 'r' }],
        }),
        event('source_processed', 'researching', {
          source_id: 's1',
          title: 't',
          chunk_count: 4,
          duplicate_of: null,
        }),
      ],
      'researching',
    );

    expect(stageState(stages, 'planning')).toBe('done');
    expect(stageState(stages, 'searching')).toBe('done');
    expect(stageState(stages, 'reading')).toBe('active');
    expect(stageState(stages, 'writing')).toBe('pending');
  });

  it('marks the furthest stage failed when the run failed there', () => {
    const stages = deriveStages(
      [
        event('planner_completed', 'researching', {
          research_goal: 'g',
          iteration: 1,
          tasks: [],
        }),
        event('search_started', 'researching', {
          task_external_id: 'market',
          query: 'q',
          provider: 'tavily',
        }),
        event('research_failed', 'failed', {
          code: 'search_provider_unavailable',
          message: 'boom',
          partial_report: false,
        }),
      ],
      'failed',
    );

    expect(stageState(stages, 'planning')).toBe('done');
    expect(stageState(stages, 'searching')).toBe('failed');
    expect(stageState(stages, 'reading')).toBe('pending');
  });

  it('never leaves a stage active once the run completed', () => {
    const stages = deriveStages(
      [
        event('report_completed', 'completed', {
          report_id: 'r1',
          word_count: 900,
          overall_confidence: 0.8,
          cost_usd: 1.2,
          coverage_caveat: null,
        }),
      ],
      'completed',
    );
    expect(stages.every((stage) => stage.state === 'done')).toBe(true);
    expect(stageProgressRatio(stages)).toBe(1);
  });

  it('reports counted detail rather than an estimate', () => {
    const stages = deriveStages(
      [event('sources_progress', 'researching', { processed: 7, discovered: 14, limit: 50 })],
      'researching',
    );
    expect(stages.find((stage) => stage.stage === 'reading')?.detail).toBe('7/14 sources');
  });
});

describe('accumulate', () => {
  it('takes the maximum of progress counters rather than summing them', () => {
    const totals = accumulate([
      event('evidence_progress', 'researching', { claims: 3, evidence: 6, verified: 1 }),
      event('evidence_progress', 'researching', { claims: 9, evidence: 21, verified: 5 }),
      // An out-of-order replay after a reconnect must not lower the counters.
      event('evidence_progress', 'researching', { claims: 4, evidence: 8, verified: 2 }),
    ]);

    expect(totals.claims).toBe(9);
    expect(totals.evidence).toBe(21);
    expect(totals.verified).toBe(5);
  });

  it('counts discrete discovery events', () => {
    const totals = accumulate([
      event('source_found', 'researching', { source_id: 'a' }),
      event('source_found', 'researching', { source_id: 'b' }),
      event('contradiction_found', 'verifying', { contradiction_id: 'c1' }),
    ]);

    expect(totals.sourcesFound).toBe(2);
    expect(totals.contradictions).toBe(1);
  });
});
