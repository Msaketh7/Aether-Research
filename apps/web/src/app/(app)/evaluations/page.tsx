'use client';

import type { MetricValue } from '@aether/shared-types';
import { BarChart3, FlaskConical, Info } from 'lucide-react';
import { EmptyState } from '@/components/common/empty-state';
import { ErrorState } from '@/components/common/error-state';
import { PageHeader } from '@/components/common/page-header';
import { SectionCard } from '@/components/common/section-card';
import { StatTile } from '@/components/common/stat-tile';
import { MetricGrid } from '@/components/evaluations/metric-grid';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { API_MODE } from '@/lib/api/config';
import { useEvaluations, useSystemMetrics } from '@/lib/api/queries';
import {
  formatCost,
  formatCount,
  formatDateTime,
  formatDuration,
  formatPercent,
  formatTokens,
} from '@/lib/format';

/**
 * Quality and system dashboards.
 *
 * The rule from docs/evaluation.md is enforced in the UI: only measured values
 * are shown, unmeasured metrics render as "not measured", and while the app
 * runs on fixtures that fact is stated at the top of the page rather than left
 * for the reader to infer.
 */

const GROUPS: Array<{ title: string; keys: string[] }> = [
  { title: 'Retrieval', keys: ['recall_at_10', 'precision_at_10', 'mrr', 'ndcg'] },
  {
    title: 'Generation',
    keys: ['groundedness', 'citation_precision', 'citation_recall', 'faithfulness', 'correctness'],
  },
  {
    title: 'Agent behaviour',
    keys: [
      'task_completion',
      'tool_selection',
      'unnecessary_tool_calls',
      'planning_quality',
      'recovery_rate',
    ],
  },
  { title: 'System', keys: ['latency_p95', 'cost_per_run', 'failure_rate'] },
];

function group(metrics: MetricValue[], keys: string[]): MetricValue[] {
  return keys
    .map((key) => metrics.find((metric) => metric.key === key))
    .filter((metric): metric is MetricValue => metric !== undefined);
}

export default function EvaluationsPage() {
  const evaluations = useEvaluations();
  const system = useSystemMetrics('24h');

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Evaluations"
        description="Measured quality of retrieval, generation and agent behaviour, plus live system metrics."
      />

      {API_MODE === 'mock' ? (
        <Alert variant="warning" className="mb-5">
          <Info aria-hidden />
          <AlertDescription>
            <span className="font-medium text-foreground">These are fixture values. </span>
            The evaluation suite is built in Phase 18 and has not been executed. The dataset version
            and commit below both read <code className="font-mono">fixture</code> for that reason.
            No number here is a measured benchmark result.
          </AlertDescription>
        </Alert>
      ) : null}

      <section aria-label="System metrics" className="mb-6">
        <h2 className="mb-3 text-sm font-semibold">System — last 24 hours</h2>
        {system.isPending ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-24" />
            ))}
          </div>
        ) : system.isError ? (
          <ErrorState error={system.error} onRetry={() => void system.refetch()} />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatTile
              label="Research success rate"
              value={formatPercent(system.data.research_success_rate, 1)}
              hint={`Failure ${formatPercent(system.data.research_failure_rate, 1)}`}
            />
            <StatTile
              label="Run latency P95"
              value={formatDuration(system.data.latency_p95_seconds)}
              hint={`P50 ${formatDuration(system.data.latency_p50_seconds)} · P99 ${formatDuration(system.data.latency_p99_seconds)}`}
            />
            <StatTile
              label="Cache hit rate"
              value={formatPercent(system.data.cache_hit_rate, 1)}
              hint={`${formatTokens(system.data.total_tokens)} tokens · ${formatCost(system.data.total_cost_usd)}`}
            />
            <StatTile
              label="Queue depth"
              value={formatCount(system.data.queue_depth)}
              hint={`${formatCount(system.data.active_workers)} active workers`}
            />
          </div>
        )}
      </section>

      {evaluations.isPending ? (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-32" />
          <Skeleton className="h-64" />
        </div>
      ) : evaluations.isError ? (
        <ErrorState error={evaluations.error} onRetry={() => void evaluations.refetch()} />
      ) : evaluations.data.latest === null ? (
        <EmptyState
          icon={FlaskConical}
          title="No benchmark has been run"
          description="Once the evaluation suite runs against a build, its metrics, thresholds and per-case results appear here. Nothing is shown until then."
        />
      ) : (
        <div className="flex flex-col gap-6">
          <Card className="p-4">
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs">
              <Badge variant={evaluations.data.latest.passed ? 'success' : 'danger'}>
                {evaluations.data.latest.passed ? 'Gate passed' : 'Gate failed'}
              </Badge>
              <span className="text-muted-foreground">
                {evaluations.data.latest.passed_count}/{evaluations.data.latest.case_count} cases
              </span>
              <span className="font-mono text-muted-foreground">
                dataset {evaluations.data.latest.dataset_version} · commit{' '}
                {evaluations.data.latest.git_sha}
              </span>
              <span className="text-muted-foreground">
                {formatDateTime(evaluations.data.latest.completed_at)}
              </span>
              <span className="ml-auto font-mono text-muted-foreground">
                judge {evaluations.data.latest.model_config.judge ?? '—'}
              </span>
            </div>
          </Card>

          {GROUPS.map((section) => {
            const metrics = group(evaluations.data.latest!.metrics, section.keys);
            if (metrics.length === 0) return null;
            return <MetricGrid key={section.title} title={section.title} metrics={metrics} />;
          })}

          <SectionCard title="Cases" contentClassName="px-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Case</TableHead>
                  <TableHead className="w-24">Kind</TableHead>
                  <TableHead className="w-20">Result</TableHead>
                  <TableHead className="w-24 text-right">Duration</TableHead>
                  <TableHead className="min-w-64">Notes</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {evaluations.data.cases.map((testCase) => (
                  <TableRow key={testCase.case_id} data-testid="eval-case">
                    <TableCell>
                      <p className="font-mono text-xs">{testCase.case_id}</p>
                      <p className="mt-0.5 text-xs text-muted-foreground">{testCase.question}</p>
                    </TableCell>
                    <TableCell className="text-xs capitalize">{testCase.kind}</TableCell>
                    <TableCell>
                      <Badge variant={testCase.passed ? 'success' : 'danger'}>
                        {testCase.passed ? 'pass' : 'fail'}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs tabular-nums">
                      {formatDuration(testCase.duration_seconds)}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {testCase.failures.length > 0 ? testCase.failures.join(' ') : '—'}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </SectionCard>

          <SectionCard title="Benchmark history" contentClassName="px-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Completed</TableHead>
                  <TableHead className="w-24">Result</TableHead>
                  <TableHead className="w-28">Cases</TableHead>
                  <TableHead className="w-32 text-right">Groundedness</TableHead>
                  <TableHead className="w-36 text-right">Citation precision</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {evaluations.data.history.map((historic) => {
                  const groundedness = historic.metrics.find((m) => m.key === 'groundedness');
                  const precision = historic.metrics.find((m) => m.key === 'citation_precision');
                  return (
                    <TableRow key={historic.id}>
                      <TableCell className="text-xs">
                        {formatDateTime(historic.completed_at)}
                      </TableCell>
                      <TableCell>
                        <Badge variant={historic.passed ? 'success' : 'danger'}>
                          {historic.passed ? 'pass' : 'fail'}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs tabular-nums">
                        {historic.passed_count}/{historic.case_count}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs tabular-nums">
                        {formatPercent(groundedness?.value ?? null, 1)}
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs tabular-nums">
                        {formatPercent(precision?.value ?? null, 1)}
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </SectionCard>

          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <BarChart3 className="size-3.5" aria-hidden />
            Methodology, metric definitions and threshold rationale are in docs/evaluation.md.
          </p>
        </div>
      )}
    </div>
  );
}
