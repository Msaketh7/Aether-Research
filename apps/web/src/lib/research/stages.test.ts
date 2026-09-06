import type { ResearchEvent, ResearchEventType, RunStatus } from '@aether/shared-types';
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
    expect(stages).toHaveLength(7);
    expect(stages.every((stage) => stage.state === 'pending')).toBe(true);
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
