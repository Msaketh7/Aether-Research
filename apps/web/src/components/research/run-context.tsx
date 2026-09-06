'use client';

import type { ResearchEvent, ResearchRun, StageProgress } from '@aether/shared-types';
import { createContext, useContext, useMemo, type ReactNode } from 'react';
import { useResearchRun } from '@/lib/api/queries';
import { deriveStages } from '@/lib/research/stages';
import { useResearchEvents, type StreamState } from '@/lib/sse/use-research-events';

/**
 * Run-scoped state, shared by every tab of a run.
 *
 * The SSE subscription lives at the run layout rather than on one page, so
 * moving between Overview, Activity, Sources, Evidence and Report does not tear
 * down and re-establish the stream - which would lose events and restart the
 * feed from an arbitrary point.
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
}

const RunContext = createContext<RunContextValue | null>(null);

const TERMINAL = new Set(['completed', 'failed', 'cancelled']);

export function RunProvider({ runId, children }: { runId: string; children: ReactNode }) {
  const { data: run, isPending, error, refetch } = useResearchRun(runId);

  // A finished run has nothing left to stream; its trace comes from the
  // activity endpoint instead, so no connection is opened.
  const isLive = run !== undefined && !TERMINAL.has(run.status);
  const { events, state } = useResearchEvents(runId, { enabled: isLive });

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
    }),
    [runId, run, isPending, error, refetch, events, state, isLive],
  );

  return <RunContext.Provider value={value}>{children}</RunContext.Provider>;
}

export function useRunContext(): RunContextValue {
  const context = useContext(RunContext);
  if (!context) throw new Error('useRunContext must be used inside a RunProvider');
  return context;
}
