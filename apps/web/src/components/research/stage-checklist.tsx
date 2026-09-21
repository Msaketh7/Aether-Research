'use client';

import type { StageProgress } from '@aether/shared-types';
import { Check, LoaderCircle, X } from 'lucide-react';
import { STAGE_LABELS } from '@/lib/research/stages';
import { cn } from '@/lib/utils';

/**
 * The activity checklist from PRD 6.1: Planning, Searching, Reading sources,
 * Extracting evidence, Verifying claims, Detecting contradictions, Writing
 * report.
 *
 * Derived from the event stream (see lib/research/stages.ts) rather than sent
 * by the server, so it cannot contradict the trace on the activity page.
 *
 * Drawn as a timeline rather than a list of ticks. The connector between two
 * markers is what makes seven independent rows read as one process with a
 * front edge, and it fills as the run advances, so the shape of the rail says
 * how far along the run is before any of the labels have been read.
 */
export function StageChecklist({
  stages,
  className,
}: {
  stages: StageProgress[];
  className?: string;
}) {
  return (
    <ol className={cn('flex flex-col', className)} aria-label="Research progress">
      {stages.map((stage, index) => {
        const done = stage.state === 'done';
        const active = stage.state === 'active';
        const failed = stage.state === 'failed';
        const last = index === stages.length - 1;

        return (
          <li
            key={stage.stage}
            data-stage={stage.stage}
            data-state={stage.state}
            aria-current={active ? 'step' : undefined}
            className="relative flex gap-3 pb-1 last:pb-0"
          >
            {/* The connector. Its colour is the state of the step above it, so
                the filled part of the rail always ends at the current step. */}
            {last ? null : (
              <span
                className={cn(
                  'absolute left-[11px] top-6 h-[calc(100%-1rem)] w-px',
                  'transition-colors duration-[var(--duration-slow)] ease-[var(--ease-out-soft)]',
                  done ? 'bg-success/45' : failed ? 'bg-destructive/40' : 'bg-border',
                )}
                aria-hidden
              />
            )}

            <span
              className={cn(
                'relative z-10 mt-0.5 flex size-[22px] shrink-0 items-center justify-center rounded-full border',
                'transition-[background-color,border-color,box-shadow] duration-[var(--duration-base)] ease-[var(--ease-out-soft)]',
                done && 'border-success/40 bg-success/15 text-success-strong',
                failed && 'border-destructive/40 bg-destructive/15 text-destructive-strong',
                active && 'border-primary/50 bg-primary/12 text-primary shadow-glow',
                stage.state === 'pending' && 'border-border bg-card text-muted-foreground/45',
              )}
              aria-hidden
            >
              {done ? (
                <Check className="size-3" />
              ) : failed ? (
                <X className="size-3" />
              ) : active ? (
                <LoaderCircle className="size-3 animate-spin" data-motion="loop" />
              ) : (
                <span className="size-1.5 rounded-full bg-current" />
              )}
            </span>

            <span
              className={cn(
                'flex min-w-0 flex-1 items-center gap-2 rounded-lg px-2 py-1 text-sm',
                'transition-colors duration-[var(--duration-base)] ease-[var(--ease-out-soft)]',
                active && 'bg-accent/60',
              )}
            >
              <span
                className={cn(
                  'min-w-0 flex-1 truncate',
                  done && 'text-foreground',
                  active && 'font-medium text-foreground',
                  stage.state === 'pending' && 'text-muted-foreground',
                  failed && 'text-destructive-strong',
                )}
              >
                {STAGE_LABELS[stage.stage]}
              </span>

              {stage.detail ? (
                <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                  {stage.detail}
                </span>
              ) : null}
            </span>

            <span className="sr-only">{stage.state}</span>
          </li>
        );
      })}
    </ol>
  );
}
