import { describe, expect, it } from 'vitest';
import { buildDataset, type RunSpec } from './fixtures';
import { stableUuid } from './rng';
import { buildTimeline, statusAt } from './timeline';

/**
 * The fixtures stand in for the real backend, so they have to satisfy the same
 * invariants the citation validator will enforce in Phase 12. A fixture that
 * cheats would let the UI be built against data the real system can never
 * produce.
 */

const CREATED = new Date('2026-09-01T10:00:00.000Z');

function spec(overrides: Partial<RunSpec> = {}): RunSpec {
  return {
    id: stableUuid('test:run'),
    title: 'Test run',
    question: 'Compare the major AI inference infrastructure companies.',
    mode: 'deep',
    depth: 3,
    domains: [],
    dateRangeStart: null,
    dateRangeEnd: null,
    createdAt: CREATED,
    startedAt: CREATED,
    status: 'completed',
    coverage: 1,
    coverageCaveat: null,
    parentRunId: null,
    ...overrides,
  };
}

describe('buildDataset', () => {
  it('is deterministic across builds', () => {
    expect(JSON.stringify(buildDataset(spec(), true))).toBe(
      JSON.stringify(buildDataset(spec(), true)),
    );
  });

  it('links every evidence span to a source in the run', () => {
    const dataset = buildDataset(spec(), true);
    const sourceIds = new Set(dataset.sources.map((source) => source.id));

    for (const claim of dataset.claims) {
      for (const evidence of [...claim.supporting, ...claim.refuting]) {
        expect(sourceIds.has(evidence.source_id)).toBe(true);
        expect(evidence.claim_id).toBe(claim.id);
        expect(evidence.span_text.length).toBeGreaterThan(0);
      }
    }
  });

  it('resolves every citation to a claim, an evidence span and a source', () => {
    const dataset = buildDataset(spec(), true);
    expect(dataset.report).not.toBeNull();

    const claimIds = new Set(dataset.claims.map((claim) => claim.id));
    const sourceIds = new Set(dataset.sources.map((source) => source.id));

    for (const citation of dataset.report!.citations) {
      expect(claimIds.has(citation.claim_id)).toBe(true);
      expect(sourceIds.has(citation.source_id)).toBe(true);
      expect(citation.quote.length).toBeGreaterThan(0);
    }
  });

  it('emits no citation marker that the citation list cannot resolve', () => {
    const dataset = buildDataset(spec(), true);
    const ordinals = new Set(dataset.report!.citations.map((citation) => citation.ordinal));

    for (const section of dataset.report!.sections) {
      for (const match of section.content_md.matchAll(/\[(\d+)\]/g)) {
        // The references section lists ordinals as "1. ..." rather than "[1]".
        expect(ordinals.has(Number(match[1]))).toBe(true);
      }
    }
  });

  it('generates the references section from the citations, not by hand', () => {
    const dataset = buildDataset(spec(), true);
    const references = dataset.report!.sections.find((section) => section.kind === 'references');
    expect(references).toBeDefined();

    const lines = references!.content_md.split('\n').filter(Boolean);
    expect(lines).toHaveLength(dataset.report!.citations.length);
    for (const citation of dataset.report!.citations) {
      expect(references!.content_md).toContain(citation.source_url);
    }
  });

  it('numbers the answer against the same citations the report uses', () => {
    const dataset = buildDataset(spec(), true);
    const ordinals = new Set(dataset.report!.citations.map((citation) => citation.ordinal));

    expect(dataset.answer).not.toBeNull();
    const markers = [...dataset.answer!.content_md.matchAll(/\[(\d+)\]/g)];
    expect(markers.length).toBeGreaterThan(0);
    // The whole reason the answer is worth citing: its `[n]` resolves through
    // the report's citation list. Numbered against anything else it would point
    // at the wrong source while still looking correct.
    for (const match of markers) {
      expect(ordinals.has(Number(match[1]))).toBe(true);
    }
  });

  it('writes no answer for a run that never reached the answerer', () => {
    // A failed run stops during discovery and a cancelled one before the
    // answerer, which is what the real graph does with both.
    expect(buildDataset(spec({ status: 'failed' }), true).answer).toBeNull();
    expect(buildDataset(spec({ status: 'cancelled' }), true).answer).toBeNull();
  });

  it('produces no report for a failed run', () => {
    const dataset = buildDataset(spec({ status: 'failed' }), true);
    expect(dataset.report).toBeNull();
    expect(dataset.run.has_report).toBe(false);
    expect(dataset.run.error?.code).toBe('search_provider_unavailable');
  });

  it('records a second iteration for deep runs and one for quick runs', () => {
    expect(buildDataset(spec({ mode: 'deep' }), true).plan.iteration).toBe(2);
    expect(buildDataset(spec({ mode: 'quick', coverage: 0.4 }), false).plan.iteration).toBe(1);
  });

  it('counts claims per source from the evidence, not from a stored guess', () => {
    const dataset = buildDataset(spec(), true);
    for (const source of dataset.sources) {
      const actual = dataset.claims.filter((claim) =>
        claim.supporting.some((evidence) => evidence.source_id === source.id),
      ).length;
      expect(source.claim_count).toBe(actual);
    }
  });
});

describe('buildTimeline', () => {
  it('produces strictly increasing sequence numbers and non-decreasing offsets', () => {
    const timeline = buildTimeline(buildDataset(spec(), true));
    expect(timeline.length).toBeGreaterThan(20);

    timeline.forEach((entry, index) => {
      expect(entry.event.seq).toBe(index + 1);
      if (index > 0) {
        expect(entry.offsetMs).toBeGreaterThanOrEqual(timeline[index - 1]!.offsetMs);
      }
    });
  });

  it('ends a completed run with report_completed', () => {
    const timeline = buildTimeline(buildDataset(spec(), true));
    expect(timeline.at(-1)?.event.type).toBe('report_completed');
    expect(timeline.at(-1)?.event.status).toBe('completed');
  });

  it('ends a failed run with research_failed and never reaches synthesis', () => {
    const timeline = buildTimeline(buildDataset(spec({ status: 'failed' }), true));
    expect(timeline.at(-1)?.event.type).toBe('research_failed');
    expect(timeline.some((entry) => entry.event.type === 'synthesis_started')).toBe(false);
  });

  it('streams the answer in pieces that reassemble into the stored one', () => {
    const dataset = buildDataset(spec(), true);
    const timeline = buildTimeline(dataset);
    const deltas = timeline.filter((entry) => entry.event.type === 'answer_delta');

    // More than one, or the mock would let a client ship that only works when
    // the whole answer arrives at once.
    expect(deltas.length).toBeGreaterThan(1);
    const assembled = deltas
      .map((entry) => (entry.event as { payload: { text: string } }).payload.text)
      .join('');
    expect(assembled).toBe(dataset.answer!.content_md);
  });

  it('finishes the answer before it starts writing the report', () => {
    const timeline = buildTimeline(buildDataset(spec(), true));
    const at = (type: string) => timeline.findIndex((entry) => entry.event.type === type);

    // The order the graph runs them in, and the reason the answer is worth
    // streaming: the reader has it while the report is still being assembled.
    expect(at('answer_started')).toBeGreaterThan(-1);
    expect(at('answer_started')).toBeLessThan(at('answer_completed'));
    expect(at('answer_completed')).toBeLessThan(at('synthesis_started'));
  });

  it('only announces sources that exist in the dataset', () => {
    const dataset = buildDataset(spec(), true);
    const timeline = buildTimeline(dataset);
    const known = new Set(dataset.sources.map((source) => source.id));

    for (const entry of timeline) {
      if (entry.event.type !== 'source_found') continue;
      expect(known.has(entry.event.payload.source_id)).toBe(true);
    }
  });

  it('reports the status implied by elapsed time', () => {
    const dataset = buildDataset(spec(), true);
    const timeline = buildTimeline(dataset);

    expect(statusAt(timeline, -1, 'queued')).toBe('queued');
    expect(statusAt(timeline, 0, 'queued')).toBe('planning');
    expect(statusAt(timeline, dataset.durationMs, 'queued')).toBe('completed');
  });
});
