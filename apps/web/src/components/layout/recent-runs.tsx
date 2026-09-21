'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { Skeleton } from '@/components/ui/skeleton';
import { useResearchList } from '@/lib/api/queries';
import { cn } from '@/lib/utils';

/**
 * Recent runs in the navigation rail.
 *
 * A deep-research run is the unit of work in this product the way a
 * conversation is in a chat assistant, so the rail lists them: jumping between
 * two runs should not require going back to the dashboard and finding them in a
 * table. It shares `useResearchList`'s cache with the dashboard, so opening the
 * app does not fetch the same page twice.
 */

const ACTIVE: ReadonlySet<string> = new Set([
  'queued',
  'planning',
  'researching',
  'verifying',
  'synthesizing',
  'validating',
]);

export function RecentRuns({ onNavigate }: { onNavigate?: () => void }) {
  const params = useParams<{ id?: string }>();
  const runs = useResearchList({ limit: 6 });
  const currentId = typeof params?.id === 'string' ? params.id : undefined;

  if (runs.isPending) {
    return (
      <div className="flex flex-col gap-1.5 px-1" aria-hidden>
        {Array.from({ length: 4 }).map((_, index) => (
          <Skeleton key={index} className="h-7" />
        ))}
      </div>
    );
  }

  // A rail that renders its own error box would put a red panel permanently
  // down the side of every page. The dashboard reports the failure properly.
  if (runs.isError || runs.data.items.length === 0) {
    return (
      <p className="px-2 text-xs leading-relaxed text-muted-foreground">
        {runs.isError ? 'Recent runs unavailable.' : 'Your runs will appear here.'}
      </p>
    );
  }

  return (
    <ul className="flex flex-col gap-0.5">
      {runs.data.items.map((run) => {
        const current = run.id === currentId;
        const live = ACTIVE.has(run.status);

        return (
          <li key={run.id}>
            <Link
              href={`/research/${run.id}`}
              onClick={onNavigate}
              aria-current={current ? 'page' : undefined}
              title={run.title}
              className={cn(
                'group flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-xs',
                'transition-[background-color,color,transform] duration-[var(--duration-fast)] ease-[var(--ease-out-soft)]',
                'hover:translate-x-0.5',
                current
                  ? 'bg-accent font-medium text-accent-foreground'
                  : 'text-muted-foreground hover:bg-hover hover:text-foreground',
              )}
            >
              <span
                className={cn(
                  'size-1.5 shrink-0 rounded-full transition-colors duration-[var(--duration-fast)]',
                  live
                    ? 'bg-info'
                    : run.status === 'failed'
                      ? 'bg-destructive'
                      : run.status === 'completed'
                        ? 'bg-success'
                        : 'bg-muted-foreground/40',
                  live && 'animate-[breathe_2.4s_var(--ease-out-soft)_infinite]',
                )}
                data-motion={live ? 'loop' : undefined}
                aria-hidden
              />
              <span className="min-w-0 flex-1 truncate">{run.title}</span>
              {live ? <span className="sr-only">in progress</span> : null}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
