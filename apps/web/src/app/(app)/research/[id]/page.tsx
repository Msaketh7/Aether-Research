'use client';

import { AlertTriangle, ArrowRight, FileText, ListChecks, Quote } from 'lucide-react';
import Link from 'next/link';
import { ErrorState } from '@/components/common/error-state';
import { SectionCard } from '@/components/common/section-card';
import { StatTile } from '@/components/common/stat-tile';
import { AnswerPanel } from '@/components/research/answer-panel';
import { PriorityBadge } from '@/components/research/badges';
import { EventFeed } from '@/components/research/event-feed';
import { FollowUpComposer } from '@/components/research/follow-up-composer';
import { useRunContext } from '@/components/research/run-context';
import { StageChecklist } from '@/components/research/stage-checklist';
import { UsageMeter } from '@/components/research/usage-meter';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useResearchPlan, useResearchReport } from '@/lib/api/queries';
import { formatCount, formatTokens } from '@/lib/format';

const TASK_STATUS_VARIANT = {
  pending: 'outline',
  researching: 'info',
  done: 'success',
  insufficient: 'warning',
} as const;

/**
 * A run, as a conversation.
 *
 * The question at the top, the answer under it as it is written, and the box
 * for the next question under that - the shape every assistant has trained
 * people on, and the shape this product should always have had. What sat here
 * before was a dashboard about a question rather than an answer to it.
 *
 * Everything below the composer is what makes this not a chatbot: how the
 * answer was reached, what it cost, and what is still running. It is *below*
 * deliberately. A reader who wants the answer should not have to scroll past
 * four progress widgets to find it, and a reader who doubts the answer should
 * find the apparatus in one scroll rather than on another page.
 */
export default function RunConversationPage() {
  const { runId, run, isPending, error, refetch, events, stages, answer, isLive } = useRunContext();
  const plan = useResearchPlan(runId);
  // Only for the citation markers in the answer: they are numbered against the
  // report's citations, so until the report exists they resolve to nothing and
  // render unresolved. Not fetched while the run is live - there is no report
  // to fetch - which is also what keeps this off the hot path of a busy stream.
  const report = useResearchReport(runId, !isLive && (run?.has_report ?? false));

  if (error) return <ErrorState error={error} onRetry={refetch} title="Could not load this run" />;
  if (isPending || !run) return <Skeleton className="h-96" />;

  const finished = run.status === 'completed';

  return (
    <div className="flex flex-col gap-10">
      <section aria-label="Answer" className="mx-auto flex w-full max-w-3xl flex-col gap-5">
        {/*
         * The question, as the turn that started this. Rendered here as well as
         * in the run header because a conversation that opens with the reply is
         * not a conversation - and on a follow-up the header's title is derived
         * from the question rather than being it.
         */}
        <div className="flex justify-end">
          <p
            className="max-w-[46rem] text-balance rounded-2xl rounded-br-md bg-muted/70 px-4 py-2.5 text-sm leading-relaxed"
            data-testid="run-question"
          >
            {run.question}
          </p>
        </div>

        <div className="flex flex-col gap-4">
          <AnswerPanel answer={answer} citations={report.data?.citations ?? []} />

          {finished && run.has_report ? (
            <div className="flex flex-wrap items-center gap-2">
              <Button asChild size="sm" variant="outline" className="group rounded-full">
                <Link href={`/research/${runId}/report`}>
                  Read the full report
                  <ArrowRight
                    className="size-3.5 transition-transform duration-[var(--duration-base)] group-hover:translate-x-0.5"
                    aria-hidden
                  />
                </Link>
              </Button>
              <Button asChild size="sm" variant="ghost" className="rounded-full">
                <Link href={`/research/${runId}/sources`}>
                  {formatCount(run.source_count)} sources
                </Link>
              </Button>
              <Button asChild size="sm" variant="ghost" className="rounded-full">
                <Link href={`/research/${runId}/evidence`}>
                  {formatCount(run.claim_count)} claims
                </Link>
              </Button>
            </div>
          ) : null}
        </div>

        <FollowUpComposer
          runId={runId}
          disabled={!finished}
          disabledReason={
            isLive
              ? 'You can ask a follow-up once this run has answered.'
              : finished
                ? undefined
                : 'This run did not finish, so there is nothing to follow up on.'
          }
        />
      </section>

      <section aria-label="How this answer was reached" className="flex flex-col gap-5">
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
                  run.status === 'completed' ||
                  run.status === 'failed' ||
                  run.status === 'cancelled'
                    ? 'This run has finished. Its full step-by-step trace is on the Activity tab.'
                    : 'Waiting for the first event'
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
      </section>
    </div>
  );
}
