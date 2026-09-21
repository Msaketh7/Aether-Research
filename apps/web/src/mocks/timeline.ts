import type { ResearchEvent, RunStatus } from '@aether/shared-types';
import type { RunDataset } from './fixtures';

/**
 * Turns a dataset into the event stream a real worker would emit.
 *
 * Offsets are fractions of the run duration rather than absolute times, so the
 * same generator produces a plausible 22-second quick run and a 96-second deep
 * run. Every event is derived from the dataset, which means the live feed can
 * never describe a source or claim that the REST endpoints do not also return.
 */

export interface TimelineEntry {
  /** Milliseconds after the run started. */
  offsetMs: number;
  event: ResearchEvent;
}

interface Cursor {
  seq: number;
  entries: TimelineEntry[];
  runId: string;
  startedAt: number;
  durationMs: number;
}

function emit<T extends ResearchEvent['type']>(
  cursor: Cursor,
  fraction: number,
  type: T,
  status: RunStatus,
  payload: Extract<ResearchEvent, { type: T }>['payload'],
): void {
  const offsetMs = Math.round(cursor.durationMs * fraction);
  cursor.seq += 1;
  cursor.entries.push({
    offsetMs,
    event: {
      seq: cursor.seq,
      type,
      run_id: cursor.runId,
      at: new Date(cursor.startedAt + offsetMs).toISOString(),
      status,
      payload,
    } as ResearchEvent,
  });
}

/** Spread `count` items evenly across the fraction range [from, to]. */
function spread(from: number, to: number, count: number): number[] {
  if (count <= 0) return [];
  if (count === 1) return [(from + to) / 2];
  const step = (to - from) / (count - 1);
  return Array.from({ length: count }, (_, i) => from + step * i);
}

/**
 * How much of the answer one `answer_delta` carries.
 *
 * The same 48 characters the worker's `ANSWER_STREAM_CHUNK_CHARS` defaults to,
 * so the mock exercises the number of events a real run produces rather than a
 * convenient handful: a client that only works when the answer arrives in three
 * pieces is a client that breaks against the real thing.
 */
const ANSWER_CHUNK_CHARS = 48;

function answerChunks(text: string): string[] {
  const pieces: string[] = [];
  for (let index = 0; index < text.length; index += ANSWER_CHUNK_CHARS) {
    pieces.push(text.slice(index, index + ANSWER_CHUNK_CHARS));
  }
  return pieces;
}

export function buildTimeline(dataset: RunDataset): TimelineEntry[] {
  const { spec, run, plan, sources, claims, contradictions, report, answer } = dataset;
  const startedAt = (spec.startedAt ?? spec.createdAt).getTime();
  const cursor: Cursor = {
    seq: 0,
    entries: [],
    runId: spec.id,
    startedAt,
    durationMs: dataset.durationMs,
  };

  const isQuick = spec.mode === 'quick';
  const failed = spec.status === 'failed';
  const cancelled = spec.status === 'cancelled';

  emit(cursor, 0, 'research_started', 'planning', {
    question: spec.question,
    mode: spec.mode,
  });
  emit(cursor, 0.01, 'planner_started', 'planning', { iteration: 1 });

  const firstRound = plan.tasks.filter((task) => task.iteration === 1);
  const secondRound = plan.tasks.filter((task) => task.iteration === 2);

  emit(cursor, 0.05, 'planner_completed', 'researching', {
    research_goal: plan.research_goal,
    iteration: 1,
    tasks: firstRound.map((task) => ({
      external_id: task.external_id,
      question: task.question,
      priority: task.priority,
      rationale: task.rationale,
    })),
  });

  // --- iteration 1: subtasks, searches, discovery -------------------------
  const taskFractions = spread(0.07, 0.2, firstRound.length);
  firstRound.forEach((task, index) => {
    const at = taskFractions[index] ?? 0.1;
    emit(cursor, at, 'subtask_started', 'researching', {
      task_external_id: task.external_id,
      question: task.question,
    });
    emit(cursor, at + 0.005, 'search_started', 'researching', {
      task_external_id: task.external_id,
      query: task.question.slice(0, 72),
      provider: 'tavily',
    });
  });

  const roundOneSources = sources.filter(
    (source) => !secondRound.some((task) => task.external_id === source.task_external_id),
  );
  const cutoff = failed ? 0.35 : cancelled ? 0.5 : 1;

  const foundFractions = spread(0.1, 0.34, roundOneSources.length);
  roundOneSources.forEach((source, index) => {
    const at = foundFractions[index] ?? 0.2;
    if (at > cutoff) return;
    emit(cursor, at, 'source_found', 'researching', {
      source_id: source.id,
      title: source.title,
      url: source.url,
      publisher: source.publisher,
      source_type: source.source_type,
      relevance_score: source.relevance_score,
      task_external_id: source.task_external_id,
    });
  });

  const processedFractions = spread(0.16, 0.4, roundOneSources.length);
  roundOneSources.forEach((source, index) => {
    const at = processedFractions[index] ?? 0.25;
    if (at > cutoff) return;
    const duplicate = source.credibility_metadata.notes !== null;
    emit(cursor, at, 'source_processed', 'researching', {
      source_id: source.id,
      title: source.title,
      chunk_count: duplicate ? 0 : 6 + (index % 9),
      duplicate_of: duplicate ? (sources[0]?.id ?? null) : null,
    });
    if (index > 0 && index % 5 === 0) {
      emit(cursor, at + 0.002, 'sources_progress', 'researching', {
        processed: index + 1,
        discovered: roundOneSources.length,
        limit: run.limits.max_sources,
      });
    }
  });

  if (failed) {
    emit(cursor, 0.36, 'research_failed', 'failed', {
      code: 'search_provider_unavailable',
      message: 'The search provider returned 503 on three consecutive attempts.',
      partial_report: false,
    });
    return finalize(cursor.entries);
  }

  // --- evidence extraction -------------------------------------------------
  const claimFractions = spread(0.42, 0.56, claims.length);
  claims.forEach((claim, index) => {
    const at = claimFractions[index] ?? 0.5;
    if (at > cutoff) return;
    emit(cursor, at, 'claim_extracted', 'researching', {
      claim_id: claim.id,
      text: claim.text,
      confidence: claim.confidence,
      source_id: claim.supporting[0]?.source_id ?? sources[0]?.id ?? claim.id,
      task_external_id: claim.task_external_id,
    });
    if (index > 0 && index % 4 === 0) {
      emit(cursor, at + 0.002, 'evidence_progress', 'researching', {
        claims: index + 1,
        evidence: claims
          .slice(0, index + 1)
          .reduce((sum, c) => sum + c.supporting.length + c.refuting.length, 0),
        verified: claims.slice(0, index + 1).filter((c) => c.status === 'verified').length,
      });
    }
  });

  if (cancelled) {
    emit(cursor, 0.52, 'research_cancelled', 'cancelled', { cancelled_by: 'user' });
    return finalize(cursor.entries);
  }

  emit(cursor, 0.58, 'verification_started', 'verifying', { claim_count: claims.length });

  const contradictionFractions = spread(0.61, 0.67, contradictions.length);
  contradictions.forEach((contradiction, index) => {
    emit(cursor, contradictionFractions[index] ?? 0.64, 'contradiction_found', 'verifying', {
      contradiction_id: contradiction.id,
      normalized_key: contradiction.normalized_key,
      value_a: contradiction.value_a,
      value_b: contradiction.value_b,
      likely_reason: contradiction.likely_reason,
    });
  });

  emit(cursor, 0.69, 'evidence_progress', 'verifying', {
    claims: claims.length,
    evidence: claims.reduce((sum, c) => sum + c.supporting.length + c.refuting.length, 0),
    verified: claims.filter((c) => c.status === 'verified').length,
  });

  // --- critic and the bounded second iteration ----------------------------
  if (!isQuick) {
    emit(cursor, 0.71, 'critic_started', 'verifying', { iteration: 1 });

    if (secondRound.length > 0) {
      emit(cursor, 0.73, 'additional_research_requested', 'researching', {
        iteration: 2,
        reason: 'Pricing coverage lacked a unit-economics dimension.',
        new_task_count: secondRound.length,
      });
      emit(cursor, 0.735, 'iteration_started', 'researching', {
        iteration: 2,
        max_iterations: run.limits.max_iterations,
      });

      secondRound.forEach((task, index) => {
        const at = 0.75 + index * 0.01;
        emit(cursor, at, 'subtask_started', 'researching', {
          task_external_id: task.external_id,
          question: task.question,
        });
        emit(cursor, at + 0.005, 'search_started', 'researching', {
          task_external_id: task.external_id,
          query: task.question.slice(0, 72),
          provider: 'tavily',
        });
      });

      sources
        .filter((source) =>
          secondRound.some((task) => task.external_id === source.task_external_id),
        )
        .forEach((source, index) => {
          emit(cursor, 0.78 + index * 0.012, 'source_found', 'researching', {
            source_id: source.id,
            title: source.title,
            url: source.url,
            publisher: source.publisher,
            source_type: source.source_type,
            relevance_score: source.relevance_score,
            task_external_id: source.task_external_id,
          });
        });
    }
  }

  // --- the answer ----------------------------------------------------------
  // Before synthesis, exactly as the graph orders them: the reader has their
  // answer while the report is still being assembled.
  if (answer) {
    emit(cursor, 0.74, 'answer_started', 'synthesizing', {});
    const pieces = answerChunks(answer.content_md);
    const spots = spread(0.745, 0.82, pieces.length);
    pieces.forEach((text, index) => {
      emit(cursor, spots[index] ?? 0.8, 'answer_delta', 'synthesizing', {
        index: index + 1,
        text,
      });
    });
    emit(cursor, 0.825, 'answer_completed', 'synthesizing', {
      text: answer.content_md,
      model: answer.model,
      word_count: answer.word_count,
      citation_count: answer.citation_count,
      truncated: answer.truncated,
    });
  }

  // --- synthesis and validation -------------------------------------------
  const sectionCount = report?.sections.length ?? 4;
  emit(cursor, 0.85, 'synthesis_started', 'synthesizing', { section_count: sectionCount });

  const validation = report?.validation;
  emit(cursor, 0.93, 'citation_check', 'validating', {
    checked: validation?.checked ?? 0,
    valid: validation?.valid ?? 0,
    rejected: validation?.rejected ?? 0,
  });

  if (report) {
    emit(cursor, 0.99, 'report_completed', 'completed', {
      report_id: report.report.id,
      word_count: report.report.word_count,
      overall_confidence: report.report.overall_confidence,
      cost_usd: run.usage.cost_usd,
      coverage_caveat: report.report.coverage_caveat,
    });
  }

  return finalize(cursor.entries);
}

/**
 * Phases are generated in logical order, but their offset ranges overlap - a
 * source can be discovered while a later subtask is still being announced. A
 * real worker emits strictly in time order, and both the SSE handler and
 * `statusAt` depend on that, so the entries are sorted by offset and the
 * sequence numbers assigned afterwards.
 */
function finalize(entries: TimelineEntry[]): TimelineEntry[] {
  return entries
    .slice()
    .sort((a, b) => a.offsetMs - b.offsetMs)
    .map((entry, index) => ({
      offsetMs: entry.offsetMs,
      event: { ...entry.event, seq: index + 1 },
    }));
}

/** The run status implied by everything emitted up to `elapsedMs`. */
export function statusAt(
  timeline: readonly TimelineEntry[],
  elapsedMs: number,
  fallback: RunStatus,
): RunStatus {
  let status = fallback;
  for (const entry of timeline) {
    if (entry.offsetMs > elapsedMs) break;
    status = entry.event.status;
  }
  return status;
}
