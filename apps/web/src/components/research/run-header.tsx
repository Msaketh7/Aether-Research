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
  { segment: '', label: 'Overview' },
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
            'inline-flex cursor-help items-center gap-1.5 text-xs',
            connected ? 'text-success' : 'text-warning',
          )}
          data-testid="stream-indicator"
          data-state={streamState}
        >
          {connected ? (
            <Radio className="size-3.5 animate-pulse" aria-hidden />
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
    <div className="mb-5">
      <Link
        href="/dashboard"
        className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-3" aria-hidden />
        Dashboard
      </Link>

      <div className="mt-2 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">{run.title}</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">{run.question}</p>
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
                <LoaderCircle className="animate-spin" aria-hidden />
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
        <span>{formatDuration(run.usage.elapsed_seconds)} elapsed</span>
        <span>{formatCost(run.usage.cost_usd)} spent</span>
        {run.parent_run_id ? (
          <Link href={`/research/${run.parent_run_id}`} className="underline underline-offset-2">
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

      <nav
        className="mt-5 flex gap-1 overflow-x-auto border-b border-border"
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
                '-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm transition-colors',
                isActive
                  ? 'border-primary font-medium text-foreground'
                  : 'border-transparent text-muted-foreground hover:text-foreground',
              )}
            >
              {tab.label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
