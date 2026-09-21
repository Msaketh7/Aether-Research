'use client';

import type { ResearchRunSummary } from '@aether/shared-types';
import { AlertTriangle, ChevronRight, FileText, Quote } from 'lucide-react';
import Link from 'next/link';
import { Progress } from '@/components/ui/progress';
import { formatCost, formatRelativeTime, truncate } from '@/lib/format';
import { cn } from '@/lib/utils';
import { ModeBadge, RunStatusBadge } from './badges';

/**
 * Research history.
 *
 * A list of cards rather than a data table. The previous table needed six
 * columns to say what one card says in two lines, and below `sm` those columns
 * either wrapped into nonsense or pushed the page into horizontal scroll. A
 * card also gives the progress bar of a still-running job somewhere to live at
 * full width, which is the one thing a user actually watches here.
 *
 * Exactly one link per row, stretched over the whole card by the `::after`
 * overlay: a card whose title, metadata and chevron are three separate links to
 * the same place is three stops for a keyboard user and three announcements for
 * a screen reader.
 */

const ACTIVE: ReadonlySet<string> = new Set([
  'queued',
  'planning',
  'researching',
  'verifying',
  'synthesizing',
  'validating',
]);

export function RunList({ runs }: { runs: ResearchRunSummary[] }) {
  return (
    <ul className="stagger flex flex-col gap-2">
      {runs.map((run) => {
        const active = ACTIVE.has(run.status);

        return (
          <li
            key={run.id}
            data-testid="run-row"
            className={cn(
              'group relative rounded-xl border border-border bg-card p-4 shadow-e1',
              'transition-[transform,box-shadow,border-color] duration-[var(--duration-base)] ease-[var(--ease-out-soft)]',
              'hover:-translate-y-0.5 hover:border-primary/35 hover:shadow-e2',
              'focus-within:border-primary/45 focus-within:shadow-glow',
            )}
          >
            <div className="flex items-start gap-3">
              <div className="min-w-0 flex-1">
                <h3 className="text-sm font-medium leading-snug">
                  <Link
                    href={`/research/${run.id}`}
                    className="after:absolute after:inset-0 after:rounded-xl focus-visible:outline-none"
                  >
                    {run.title}
                  </Link>
                </h3>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  {truncate(run.question, 140)}
                </p>
              </div>

              <ChevronRight
                className="mt-0.5 size-4 shrink-0 text-muted-foreground/60 transition-transform duration-[var(--duration-base)] ease-[var(--ease-out-soft)] group-hover:translate-x-0.5 group-hover:text-foreground"
                aria-hidden
              />
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
              <RunStatusBadge status={run.status} />
              <ModeBadge mode={run.mode} />

              <span className="inline-flex items-center gap-1">
                <FileText className="size-3" aria-hidden />
                {run.source_count} sources
              </span>
              <span className="inline-flex items-center gap-1">
                <Quote className="size-3" aria-hidden />
                {run.claim_count} claims
              </span>
              {run.contradiction_count > 0 ? (
                <span className="inline-flex items-center gap-1 text-warning-strong">
                  <AlertTriangle className="size-3" aria-hidden />
                  {run.contradiction_count} contradiction{run.contradiction_count === 1 ? '' : 's'}
                </span>
              ) : null}

              <span className="ml-auto flex items-center gap-3">
                <span className="font-mono tabular-nums">{formatCost(run.cost_usd)}</span>
                <span>{formatRelativeTime(run.created_at)}</span>
              </span>
            </div>

            {active ? (
              <Progress
                value={Math.round(run.progress * 100)}
                className="mt-3"
                aria-label={`${run.title} progress`}
              />
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
