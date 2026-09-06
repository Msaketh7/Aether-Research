'use client';

import type { StageProgress } from '@aether/shared-types';
import { Check, CircleDashed, LoaderCircle, X } from 'lucide-react';
import { STAGE_LABELS } from '@/lib/research/stages';
import { cn } from '@/lib/utils';

/**
 * The activity checklist from PRD 6.1: Planning, Searching, Reading sources,
 * Extracting evidence, Verifying claims, Detecting contradictions, Writing
 * report.
 *
 * Derived from the event stream (see lib/research/stages.ts) rather than sent
 * by the server, so it cannot contradict the trace on the activity page.
 */
export function StageChecklist({
  stages,
  className,
}: {
  stages: StageProgress[];
  className?: string;
}) {
  return (
    <ol className={cn('flex flex-col gap-0.5', className)} aria-label="Research progress">
      {stages.map((stage) => {
        const done = stage.state === 'done';
        const active = stage.state === 'active';
        const failed = stage.state === 'failed';

        return (
          <li
            key={stage.stage}
            data-stage={stage.stage}
            data-state={stage.state}
            aria-current={active ? 'step' : undefined}
            className={cn(
              'flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm',
              active && 'bg-accent/60',
            )}
          >
            <span className="flex size-4 shrink-0 items-center justify-center" aria-hidden>
              {done ? (
                <Check className="size-4 text-success" />
              ) : failed ? (
                <X className="size-4 text-destructive" />
              ) : active ? (
                <LoaderCircle className="size-4 animate-spin text-primary" />
              ) : (
                <CircleDashed className="size-4 text-muted-foreground/50" />
              )}
            </span>

            <span
              className={cn(
                'min-w-0 flex-1 truncate',
                done && 'text-foreground',
                active && 'font-medium text-foreground',
                stage.state === 'pending' && 'text-muted-foreground',
                failed && 'text-destructive',
              )}
            >
              {STAGE_LABELS[stage.stage]}
            </span>

            {stage.detail ? (
              <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
                {stage.detail}
              </span>
            ) : null}

            <span className="sr-only">{stage.state}</span>
          </li>
        );
      })}
    </ol>
  );
}
