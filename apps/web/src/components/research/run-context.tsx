'use client';

import type { ResearchEvent, ResearchRun, StageProgress } from '@aether/shared-types';
import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { useResearchAnswer, useResearchRun } from '@/lib/api/queries';
import { deriveStages } from '@/lib/research/stages';
import {
  useResearchEvents,
  NO_STREAMED_ANSWER,
  type StreamedAnswer,
  type StreamState,
} from '@/lib/sse/use-research-events';

/**
 * Run-scoped state, shared by every tab of a run.
 *
 * The SSE subscription lives at the run layout rather than on one page, so
 * moving between the conversation, Activity, Sources, Evidence and Report does
 * not tear down and re-establish the stream - which would lose events and
 * restart the feed from an arbitrary point.
 *
 * The answer has two sources and exactly one of them is used at a time. A live
 * run is served by the stream, because that is the only copy that exists while
 * it is being written. A finished one is served from its row, because nobody
 * streamed it to this browser and a replay of a run that ended yesterday is not
 * something to open a connection for. Preferring the stream while it is live is
 * what stops a refetch replacing, mid-sentence, the text a reader is watching.
 */

interface RunContextValue {
  runId: string;
  run: ResearchRun | undefined;
  isPending: boolean;
  error: unknown;
  refetch: () => void;
  events: ResearchEvent[];
  stages: StageProgress[];
  streamState: StreamState;
  isLive: boolean;
  /** The answer, from whichever of the two sources applies. */
  answer: StreamedAnswer;
  /** The answer is still being fetched or has not been written yet. */
  answerPending: boolean;
}

const RunContext = createContext<RunContextValue | null>(null);

const TERMINAL = new Set(['completed', 'failed', 'cancelled']);

export function RunProvider({ runId, children }: { runId: string; children: ReactNode }) {
  const { data: run, isPending, error, refetch } = useResearchRun(runId);

  // A finished run has nothing left to stream; its trace comes from the
  // activity endpoint instead, so no connection is opened.
  const isLive = run !== undefined && !TERMINAL.has(run.status);
  const { events, state, answer: streamed } = useResearchEvents(runId, { enabled: isLive });

  // Fetched only when there is no stream to be served by. A run that ends while
  // this page is open keeps the streamed text - `isLive` goes false, this query
  // enables, and its result is preferred only if the stream produced nothing.
  const stored = useResearchAnswer(runId, run !== undefined && !isLive);

  const answer = useMemo<StreamedAnswer>(() => {
    if (streamed.text) return streamed;
    const row = stored.data?.answer;
    if (!row) return NO_STREAMED_ANSWER;
    return { text: row.content_md, streaming: false, complete: true, truncated: row.truncated };
  }, [streamed, stored.data]);

  const value = useMemo<RunContextValue>(
    () => ({
      runId,
      run,
      isPending,
      error,
      refetch: () => void refetch(),
      events,
      stages: deriveStages(events, run?.status ?? 'queued'),
      streamState: state,
      isLive,
      answer,
      // Pending, not empty: a run that is still researching has no answer yet
      // and one that failed before answering never will, and the two read very
      // differently on the page.
      answerPending: answer.text === '' && (isLive || stored.isPending),
    }),
    [runId, run, isPending, error, refetch, events, state, isLive, answer, stored.isPending],
  );

  return <RunContext.Provider value={value}>{children}</RunContext.Provider>;
}

export function useRunContext(): RunContextValue {
  const context = useContext(RunContext);
  if (!context) throw new Error('useRunContext must be used inside a RunProvider');
  return context;
}
