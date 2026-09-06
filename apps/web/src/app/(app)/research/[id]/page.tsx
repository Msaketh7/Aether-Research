'use client';

import { AlertTriangle, FileText, ListChecks, Quote } from 'lucide-react';
import Link from 'next/link';
import { ErrorState } from '@/components/common/error-state';
import { SectionCard } from '@/components/common/section-card';
import { StatTile } from '@/components/common/stat-tile';
import { PriorityBadge } from '@/components/research/badges';
import { EventFeed } from '@/components/research/event-feed';
import { useRunContext } from '@/components/research/run-context';
import { StageChecklist } from '@/components/research/stage-checklist';
import { UsageMeter } from '@/components/research/usage-meter';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useResearchPlan } from '@/lib/api/queries';
import { formatCount, formatTokens } from '@/lib/format';

const TASK_STATUS_VARIANT = {
  pending: 'outline',
  researching: 'info',
  done: 'success',
  insufficient: 'warning',
} as const;

/** Run overview: what is happening now, and what has been produced so far. */
export default function RunOverviewPage() {
  const { runId, run, isPending, error, refetch, events, stages } = useRunContext();
  const plan = useResearchPlan(runId);

  if (error) return <ErrorState error={error} onRetry={refetch} title="Could not load this run" />;
  if (isPending || !run) return <Skeleton className="h-96" />;

  return (
    <div className="flex flex-col gap-5">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <StatTile label="Sources" value={formatCount(run.source_count)} icon={FileText} />
        <StatTile label="Claims" value={formatCount(run.claim_count)} icon={Quote} />
        <StatTile
          label="Contradictions"
          value={formatCount(run.contradiction_count)}
          hint={run.contradiction_count > 0 ? 'Shown, never auto-resolved' : 'None detected'}
          icon={AlertTriangle}
        />
        <StatTile
          label="Tokens"
          value={formatTokens(run.usage.total_tokens)}
          hint={`Iteration ${run.usage.iterations} of ${run.limits.max_iterations}`}
          icon={ListChecks}
        />
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="flex flex-col gap-5">
          <SectionCard
            title="Progress"
            action={
              <Button asChild variant="ghost" size="sm">
                <Link href={`/research/${runId}/activity`}>Full trace</Link>
              </Button>
            }
          >
            <StageChecklist stages={stages} />
          </SectionCard>

          <SectionCard title="Live activity" contentClassName="px-2">
            <EventFeed
              events={events}
              emptyMessage={
                run.status === 'completed' || run.status === 'failed' || run.status === 'cancelled'
                  ? 'This run has finished. Its full step-by-step trace is on the Activity tab.'
                  : 'Waiting for the first event…'
              }
            />
          </SectionCard>
        </div>

        <div className="flex flex-col gap-5">
          <SectionCard title="Budget">
            <UsageMeter run={run} />
          </SectionCard>

          <SectionCard title="Research plan">
            {plan.isPending ? (
              <div className="flex flex-col gap-2">
                {Array.from({ length: 4 }).map((_, index) => (
                  <Skeleton key={index} className="h-10" />
                ))}
              </div>
            ) : plan.isError ? (
              <ErrorState error={plan.error} onRetry={() => void plan.refetch()} />
            ) : plan.data.tasks.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                The planner has not produced subtasks yet.
              </p>
            ) : (
              <ol className="flex flex-col gap-3">
                {plan.data.tasks.map((task) => (
                  <li key={task.id} className="border-l-2 border-border pl-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-xs">{task.external_id}</span>
                      <PriorityBadge priority={task.priority} />
                      <Badge variant={TASK_STATUS_VARIANT[task.status]}>{task.status}</Badge>
                      {task.iteration > 1 ? (
                        <Badge variant="outline">iteration {task.iteration}</Badge>
                      ) : null}
                    </div>
                    <p className="mt-1 text-sm leading-snug">{task.question}</p>
                    <p className="mt-1 text-xs text-muted-foreground">{task.rationale}</p>
                  </li>
                ))}
              </ol>
            )}
          </SectionCard>
        </div>
      </div>
    </div>
  );
}
