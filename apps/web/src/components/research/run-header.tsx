'use client';

import { AlertTriangle, ArrowLeft, Ban, LoaderCircle, Radio, WifiOff } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Progress } from '@/components/ui/progress';
import { Skeleton } from '@/components/ui/skeleton';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { useCancelResearch } from '@/lib/api/queries';
import { formatCost, formatDuration, formatRelativeTime } from '@/lib/format';
import { cn } from '@/lib/utils';
import { ModeBadge, RunStatusBadge } from './badges';
import { useRunContext } from './run-context';

const TABS = [
  { segment: '', label: 'Answer' },
  { segment: 'activity', label: 'Activity' },
  { segment: 'sources', label: 'Sources' },
  { segment: 'evidence', label: 'Evidence' },
  { segment: 'report', label: 'Report' },
] as const;

function StreamIndicator() {
  const { isLive, streamState } = useRunContext();
  if (!isLive) return null;

  const connected = streamState === 'open' || streamState === 'connecting';

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            'inline-flex cursor-help items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium',
            'transition-colors duration-[var(--duration-base)]',
            connected
              ? 'border-success/25 bg-success/10 text-success-strong'
              : 'border-warning/30 bg-warning/10 text-warning-strong',
          )}
          data-testid="stream-indicator"
          data-state={streamState}
        >
          {connected ? (
            <Radio
              className="size-3.5 animate-[breathe_2.4s_var(--ease-out-soft)_infinite]"
              data-motion="loop"
              aria-hidden
            />
          ) : (
            <WifiOff className="size-3.5" aria-hidden />
          )}
          {connected ? 'Live' : 'Reconnecting'}
        </span>
      </TooltipTrigger>
      <TooltipContent>
        {connected
          ? 'Connected to the run event stream.'
          : 'The event stream dropped. The page still refreshes on a timer, and the stream resumes from the last event received.'}
      </TooltipContent>
    </Tooltip>
  );
}

export function RunHeader() {
  const { runId, run, isPending, isLive } = useRunContext();
  const pathname = usePathname();
  const cancel = useCancelResearch(runId);

  if (isPending || !run) {
    return (
      <div className="mb-5 flex flex-col gap-3">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-7 w-96" />
        <Skeleton className="h-9 w-full max-w-md" />
      </div>
    );
  }

  const base = `/research/${runId}`;
  const active = (segment: string) => {
    const href = segment ? `${base}/${segment}` : base;
    return pathname === href;
  };

  return (
    <>
      {/*
       * A fragment, not a wrapper. The tab strip below is `sticky`, and a
       * sticky element can only travel inside its own parent's box - wrapped in
       * a div that ends where the header ends, it had nowhere to go and scrolled
       * away like static content. As siblings, both of these sit directly in the
       * run layout's full-height container, which is the box the strip sticks
       * within.
       */}
      <div className="mb-5">
        <Link
          href="/dashboard"
          className="group inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft
            className="size-3 transition-transform duration-[var(--duration-base)] ease-[var(--ease-out-soft)] group-hover:-translate-x-0.5"
            aria-hidden
          />
          Dashboard
        </Link>

        <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="font-display text-2xl font-semibold leading-tight sm:text-[1.75rem]">
              {run.title}
            </h1>
            {/*
             * Everywhere but the answer, where the question is the opening turn
             * of the conversation and printing it twice would read as a stutter.
             * The title is derived from the question and is not the question, so
             * the other tabs still need it.
             */}
            {active('') ? null : (
              <p className="mt-1.5 max-w-3xl text-sm leading-relaxed text-muted-foreground">
                {run.question}
              </p>
            )}
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <StreamIndicator />
            {isLive ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => cancel.mutate()}
                disabled={cancel.isPending}
                data-testid="cancel-run"
              >
                {cancel.isPending ? (
                  <LoaderCircle className="animate-spin" data-motion="loop" aria-hidden />
                ) : (
                  <Ban aria-hidden />
                )}
                Cancel
              </Button>
            ) : null}
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted-foreground">
          <RunStatusBadge status={run.status} />
          <ModeBadge mode={run.mode} />
          <span>Started {formatRelativeTime(run.started_at ?? run.created_at)}</span>
          <span className="font-mono tabular-nums">
            {formatDuration(run.usage.elapsed_seconds)} elapsed
          </span>
          <span className="font-mono tabular-nums">{formatCost(run.usage.cost_usd)} spent</span>
          {run.parent_run_id ? (
            <Link
              href={`/research/${run.parent_run_id}`}
              className="underline-grow underline-offset-2"
            >
              Follow-up to an earlier run
            </Link>
          ) : null}
        </div>

        {isLive ? (
          <Progress
            value={Math.round(run.progress * 100)}
            className="mt-3"
            aria-label="Overall run progress"
          />
        ) : null}

        {run.coverage_caveat ? (
          <Alert variant="warning" className="mt-4">
            <AlertTriangle aria-hidden />
            <AlertDescription>
              <span className="font-medium text-foreground">Coverage caveat. </span>
              {run.coverage_caveat}
            </AlertDescription>
          </Alert>
        ) : null}

        {run.error ? (
          // A paused run carries the reason it stopped, and it is not a failure:
          // the worker will take it up again from where it got to. Rendering that
          // in red would say the opposite of what `paused` means.
          <Alert variant={run.status === 'paused' ? 'warning' : 'danger'} className="mt-4">
            <AlertTriangle aria-hidden />
            <AlertDescription data-testid="run-error">
              <span className="font-medium text-foreground">
                {run.status === 'paused' ? 'Paused, and will resume' : run.error.code}.{' '}
              </span>
              {run.error.message}
            </AlertDescription>
          </Alert>
        ) : null}
      </div>

      {/*
       * Sticks under the topbar so a reader halfway down a long report can move
       * to Evidence without scrolling back up. The `-mx` / `px` pair lets the
       * blurred background reach the edge of the content column while the tabs
       * stay aligned with it.
       */}
      <nav
        className="glass sticky top-14 z-20 -mx-4 mb-5 flex gap-1 overflow-x-auto border-b border-border px-4 sm:-mx-6 sm:px-6"
        aria-label="Research sections"
      >
        {TABS.map((tab) => {
          const href = tab.segment ? `${base}/${tab.segment}` : base;
          const isActive = active(tab.segment);
          return (
            <Link
              key={tab.label}
              href={href}
              aria-current={isActive ? 'page' : undefined}
              className={cn(
                'group relative -mb-px whitespace-nowrap px-3 py-2.5 text-sm',
                'transition-colors duration-[var(--duration-fast)] ease-[var(--ease-out-soft)]',
                isActive
                  ? 'font-medium text-foreground'
                  : 'text-muted-foreground hover:text-foreground',
              )}
            >
              {tab.label}
              {/* The indicator scales from the centre rather than fading in, so
                  moving between tabs reads as one mark travelling. */}
              <span
                className={cn(
                  'absolute inset-x-2 bottom-0 h-0.5 origin-center rounded-full bg-primary',
                  'transition-transform duration-[var(--duration-base)] ease-[var(--ease-out-quick)]',
                  isActive
                    ? 'scale-x-100'
                    : 'scale-x-0 group-hover:scale-x-50 group-hover:bg-border',
                )}
                aria-hidden
              />
            </Link>
          );
        })}
      </nav>
    </>
  );
}
