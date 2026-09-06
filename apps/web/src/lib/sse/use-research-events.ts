'use client';

import {
  RESEARCH_EVENT_TYPES,
  type ResearchEvent,
  type ResearchRun,
  type RunStatus,
} from '@aether/shared-types';
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useRef, useState } from 'react';
import { apiUrl } from '@/lib/api/config';
import { queryKeys } from '@/lib/api/query-keys';

/**
 * Subscribes to a run's progress stream (ADR 0006).
 *
 * Native `EventSource` handles reconnection and replays from `Last-Event-ID`,
 * which the server honours - so this hook does not implement its own retry
 * loop. Its two jobs are (a) keep an ordered, de-duplicated, bounded buffer of
 * events for the activity feed, and (b) reconcile the TanStack Query cache so
 * the live feed and the REST snapshot never disagree.
 */

/** Terminal statuses: once seen, the stream is closed deliberately. */
const TERMINAL: ReadonlySet<RunStatus> = new Set(['completed', 'failed', 'cancelled']);

/** Cap on retained events. A long deep run can emit thousands. */
const MAX_BUFFERED_EVENTS = 500;

export type StreamState = 'idle' | 'connecting' | 'open' | 'closed' | 'error';

export interface UseResearchEventsResult {
  events: ResearchEvent[];
  state: StreamState;
  /** True once a terminal event arrived. */
  finished: boolean;
  /** Number of events dropped from the front of the buffer. */
  dropped: number;
}

export interface UseResearchEventsOptions {
  /** Skip the subscription entirely, e.g. for an already-completed run. */
  enabled?: boolean;
  /** Seed the buffer with events already persisted server-side. */
  initialEvents?: ResearchEvent[];
}

export function useResearchEvents(
  runId: string,
  options: UseResearchEventsOptions = {},
): UseResearchEventsResult {
  const { enabled = true, initialEvents } = options;
  const queryClient = useQueryClient();

  const [events, setEvents] = useState<ResearchEvent[]>(initialEvents ?? []);
  // `idle` until a connection callback fires; the returned value reports
  // `connecting` while enabled, so no state is written during the effect body.
  const [state, setState] = useState<StreamState>('idle');
  const [finished, setFinished] = useState(false);
  const [dropped, setDropped] = useState(0);

  // Guards against a duplicate replayed after a reconnect.
  const seenSeq = useRef<Set<number>>(new Set(initialEvents?.map((event) => event.seq) ?? []));

  const handleEvent = useCallback(
    (event: ResearchEvent) => {
      if (seenSeq.current.has(event.seq)) return;
      seenSeq.current.add(event.seq);

      setEvents((previous) => {
        const next = [...previous, event].sort((a, b) => a.seq - b.seq);
        return next.length <= MAX_BUFFERED_EVENTS
          ? next
          : next.slice(next.length - MAX_BUFFERED_EVENTS);
      });
      if (seenSeq.current.size > MAX_BUFFERED_EVENTS) {
        setDropped(seenSeq.current.size - MAX_BUFFERED_EVENTS);
      }

      // Every event carries the run status, so the header stays correct without
      // waiting for the polling fallback.
      queryClient.setQueryData<ResearchRun>(queryKeys.research.detail(runId), (current) =>
        current && current.status !== event.status ? { ...current, status: event.status } : current,
      );

      switch (event.type) {
        case 'planner_completed':
        case 'additional_research_requested':
          void queryClient.invalidateQueries({ queryKey: queryKeys.research.plan(runId) });
          break;
        case 'sources_progress':
          void queryClient.invalidateQueries({ queryKey: ['research', 'sources', runId] });
          break;
        case 'evidence_progress':
        case 'contradiction_found':
          void queryClient.invalidateQueries({ queryKey: ['research', 'evidence', runId] });
          break;
        case 'citation_check':
          void queryClient.invalidateQueries({ queryKey: queryKeys.research.activity(runId) });
          break;
        case 'report_completed':
        case 'research_failed':
        case 'research_cancelled':
          setFinished(true);
          void queryClient.invalidateQueries({ queryKey: queryKeys.research.detail(runId) });
          void queryClient.invalidateQueries({ queryKey: queryKeys.research.report(runId) });
          void queryClient.invalidateQueries({ queryKey: queryKeys.research.activity(runId) });
          void queryClient.invalidateQueries({ queryKey: queryKeys.research.stats() });
          break;
        default:
          break;
      }

      if (TERMINAL.has(event.status)) setFinished(true);
    },
    [queryClient, runId],
  );

  useEffect(() => {
    if (!enabled || !runId) return;
    if (typeof window === 'undefined' || typeof EventSource === 'undefined') return;

    const source = new EventSource(apiUrl(`/research/${runId}/events`), {
      withCredentials: true,
    });

    const onOpen = () => setState('open');
    const onError = () => {
      // EventSource retries on its own; `error` also fires on a normal server
      // close, so this is a status hint rather than a fatal condition.
      setState('error');
    };

    const parse = (raw: MessageEvent<string>) => {
      try {
        handleEvent(JSON.parse(raw.data) as ResearchEvent);
      } catch {
        // A malformed frame must not take the stream down; the polling
        // fallback in useResearchRun still converges on the truth.
      }
    };

    source.addEventListener('open', onOpen);
    source.addEventListener('error', onError);
    for (const type of RESEARCH_EVENT_TYPES) {
      source.addEventListener(type, parse as EventListener);
    }

    return () => {
      source.removeEventListener('open', onOpen);
      source.removeEventListener('error', onError);
      for (const type of RESEARCH_EVENT_TYPES) {
        source.removeEventListener(type, parse as EventListener);
      }
      source.close();
    };
  }, [enabled, runId, handleEvent]);

  return {
    events,
    state: enabled ? (state === 'idle' ? 'connecting' : state) : 'idle',
    finished,
    dropped,
  };
}
