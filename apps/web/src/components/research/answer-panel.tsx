'use client';

import type { Citation } from '@aether/shared-types';
import { Scissors, Sparkles } from 'lucide-react';
import { STAGE_LABELS } from '@/lib/research/stages';
import { cn } from '@/lib/utils';
import type { StreamedAnswer } from '@/lib/sse/use-research-events';
import { useRunContext } from './run-context';
import { MarkdownBody } from './report-view';

/**
 * The answer, as it is written.
 *
 * This is the assistant's turn in the conversation: the thing a person asked
 * for, above the box they asked in. Three states, and each is a different
 * sentence to the reader rather than a different spinner:
 *
 * - **nothing yet** - the run is still gathering material, and what it is doing
 *   right now is said in words, because a progress bar with no nouns in it is
 *   indistinguishable from a hung page;
 * - **arriving** - the text so far, with a caret, because a partial answer that
 *   looks finished is a partial answer somebody will act on;
 * - **finished** - the answer, its citations resolved against the report once
 *   there is one.
 *
 * Markers resolve to nothing until the report exists, which is most of the time
 * this component is on screen. They render unresolved rather than disappearing:
 * that is the honest rendering of a citation whose chain has not been checked,
 * and it is the same rendering the report gives one that failed the check.
 */

/** The stage the run is in right now, in the words the checklist uses. */
function useCurrentStageLabel(): string | null {
  const { stages } = useRunContext();
  const active = stages.find((stage) => stage.state === 'active');
  if (!active) return null;
  const detail = active.detail ? ` · ${active.detail}` : '';
  return `${STAGE_LABELS[active.stage]}${detail}`;
}

function Working() {
  const { run } = useRunContext();
  const label = useCurrentStageLabel();
  const queued = run?.status === 'queued';

  return (
    <div
      className="flex items-center gap-2.5 text-sm text-muted-foreground"
      data-testid="answer-working"
      role="status"
      aria-live="polite"
    >
      <Sparkles
        className="size-4 shrink-0 animate-[breathe_2.4s_var(--ease-out-soft)_infinite] text-primary"
        data-motion="loop"
        aria-hidden
      />
      <span className="truncate">
        {queued ? 'Queued — waiting for a worker' : (label ?? 'Researching')}
      </span>
    </div>
  );
}

/**
 * A caret while text is arriving.
 *
 * Inline with the prose rather than pinned under it, so it sits at the end of
 * the sentence being written. `aria-hidden` because a screen reader is served
 * by the live region around the answer, not by a blinking block.
 */
function Caret() {
  return (
    <span
      className="ml-0.5 inline-block h-[1em] w-[0.45em] translate-y-[0.12em] animate-pulse rounded-[1px] bg-primary align-baseline"
      data-motion="loop"
      data-testid="answer-caret"
      aria-hidden
    />
  );
}

export function AnswerPanel({
  answer,
  citations = [],
  className,
}: {
  answer: StreamedAnswer;
  citations?: readonly Citation[];
  className?: string;
}) {
  const { run, isLive, answerPending } = useRunContext();
  const failed = run?.status === 'failed';
  const cancelled = run?.status === 'cancelled';

  if (!answer.text) {
    if (isLive || answerPending) return <Working />;
    return (
      <p className="text-sm text-muted-foreground" data-testid="answer-absent">
        {failed
          ? 'This run stopped before it could answer. What it did find is on the Sources and Evidence tabs.'
          : cancelled
            ? 'This run was cancelled before it answered.'
            : 'This run produced no answer. Its researchers found nothing to answer from.'}
      </p>
    );
  }

  return (
    <div className={cn('flex flex-col gap-3', className)}>
      {/*
       * `aria-busy` rather than a live region over the whole answer: announcing
       * every arriving fragment would read the answer aloud several times.
       * The reader is told when it settles.
       */}
      <div
        data-testid="answer-body"
        data-streaming={answer.streaming ? 'true' : 'false'}
        aria-busy={answer.streaming}
      >
        <MarkdownBody
          content={answer.text}
          citations={citations}
          className="text-[0.95rem] leading-relaxed [&>p:first-child]:mt-0"
        />
        {answer.streaming ? <Caret /> : null}
      </div>

      {/*
       * The coverage caveat is not repeated here. The run header already carries
       * it as an alert above every tab, and a limit that ended discovery is a
       * fact about the run rather than about this paragraph.
       */}
      {answer.truncated ? (
        <p
          className="flex items-center gap-1.5 text-xs text-warning-strong"
          data-testid="answer-truncated"
        >
          <Scissors className="size-3.5 shrink-0" aria-hidden />
          The answer reached its length limit and stops early. The full report has the rest.
        </p>
      ) : null}
    </div>
  );
}
