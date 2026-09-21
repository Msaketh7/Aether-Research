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
 * loop. Its three jobs are (a) keep an ordered, de-duplicated, bounded buffer of
 * events for the activity feed, (b) assemble the answer out of the pieces it
 * arrives in, and (c) reconcile the TanStack Query cache so the live feed and
 * the REST snapshot never disagree.
 *
 * The answer is kept apart from the event buffer on purpose. Its pieces are not
 * activity - nobody wants forty "a fragment of a sentence arrived" rows in a
 * trace - and there are enough of them to push real events out of a buffer that
 * is capped for a reason.
 */

/** Terminal statuses: once seen, the stream is closed deliberately. */
const TERMINAL: ReadonlySet<RunStatus> = new Set(['completed', 'failed', 'cancelled']);

/** Cap on retained events. A long deep run can emit thousands. */
const MAX_BUFFERED_EVENTS = 500;

export type StreamState = 'idle' | 'connecting' | 'open' | 'closed' | 'error';

/**
 * The answer as the stream has delivered it so far.
 *
 * `text` is what to render. While `streaming` is true it is a partial answer
 * and grows; once `complete` is true it is the whole answer, replaced by the
 * authoritative copy `answer_completed` carries - so a piece dropped by a slow
 * subscriber heals instead of leaving a hole mid-sentence.
 */
export interface StreamedAnswer {
  text: string;
  /** Pieces are still arriving. */
  streaming: boolean;
  /** The run said the answer is finished. */
  complete: boolean;
  /** The model stopped at its output ceiling, so the answer ends early. */
  truncated: boolean;
}

export const NO_STREAMED_ANSWER: StreamedAnswer = {
  text: '',
  streaming: false,
  complete: false,
  truncated: false,
};

export interface UseResearchEventsResult {
  events: ResearchEvent[];
  state: StreamState;
  /** The answer assembled from the pieces received so far. */
  answer: StreamedAnswer;
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
  const [answer, setAnswer] = useState<StreamedAnswer>(NO_STREAMED_ANSWER);
  // `idle` until a connection callback fires; the returned value reports
  // `connecting` while enabled, so no state is written during the effect body.
  const [state, setState] = useState<StreamState>('idle');
  const [finished, setFinished] = useState(false);
  const [dropped, setDropped] = useState(0);

  // Guards against a duplicate replayed after a reconnect.
  const seenSeq = useRef<Set<number>>(new Set(initialEvents?.map((event) => event.seq) ?? []));
  // Events admitted to the buffer, which is not the same as events seen: the
  // answer's pieces are routed into the answer and never reach it. Counted
  // separately so `dropped` reports what the feed actually lost rather than
  // every sequence number that went past.
  const buffered = useRef(initialEvents?.length ?? 0);

  const handleEvent = useCallback(
    (event: ResearchEvent) => {
      if (seenSeq.current.has(event.seq)) return;
      seenSeq.current.add(event.seq);

      // Every event carries the run status, so the header stays correct without
      // waiting for the polling fallback. Done before the answer is handled,
      // because the answer's own events are the first to report `synthesizing`.
      queryClient.setQueryData<ResearchRun>(queryKeys.research.detail(runId), (current) =>
        current && current.status !== event.status ? { ...current, status: event.status } : current,
      );

      // The answer, assembled here and kept out of the event buffer. A replayed
      // stream delivers these in sequence order, so appending is correct on a
      // reconnect as well as live.
      switch (event.type) {
        case 'answer_started':
          // Always a reset. A worker that crashed mid-answer leaves a partial
          // one on screen and the retry writes a *different* answer, which must
          // replace it rather than continue it.
          setAnswer({ text: '', streaming: true, complete: false, truncated: false });
          return;
        case 'answer_delta': {
          const { text } = event.payload;
          setAnswer((previous) => ({
            ...previous,
            text: previous.text + text,
            streaming: true,
          }));
          return;
        }
        case 'answer_completed': {
          const { text, truncated } = event.payload;
          // The authoritative copy replaces what was accumulated rather than
          // being compared with it: a subscriber that missed a piece has a hole
          // in the middle of a sentence, and this is what closes it.
          setAnswer({ text, streaming: false, complete: true, truncated });
          // Whatever was fetched before the run answered is now stale. Nothing
          // usually refetches - the query is disabled under a live stream - but
          // a second tab reading the same run is not under this one.
          void queryClient.invalidateQueries({ queryKey: queryKeys.research.answer(runId) });
          return;
        }
        default:
          break;
      }

      buffered.current += 1;
      setEvents((previous) => {
        const next = [...previous, event].sort((a, b) => a.seq - b.seq);
        return next.length <= MAX_BUFFERED_EVENTS
          ? next
          : next.slice(next.length - MAX_BUFFERED_EVENTS);
      });
      if (buffered.current > MAX_BUFFERED_EVENTS) {
        setDropped(buffered.current - MAX_BUFFERED_EVENTS);
      }

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
    answer,
    finished,
    dropped,
  };
}
