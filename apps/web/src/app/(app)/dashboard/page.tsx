'use client';

import { Activity, CircleDollarSign, FileSearch, Quote, Search, Zap } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';
import { EmptyState } from '@/components/common/empty-state';
import { ErrorState } from '@/components/common/error-state';
import { PageHeader } from '@/components/common/page-header';
import { StatTile } from '@/components/common/stat-tile';
import { RunTable } from '@/components/research/run-table';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { formatCost, formatCount, formatDuration } from '@/lib/format';
import { useDashboardStats, useResearchList } from '@/lib/api/queries';

/** Suggested starting points, so an empty product is not a blank page. */
const QUICK_STARTS = [
  {
    label: 'Competitive landscape',
    question:
      'Compare the major AI inference infrastructure companies. Analyze their products, technology, pricing, funding, financial performance, recent announcements, risks, competitive advantages, and market opportunities.',
  },
  {
    label: 'Technology assessment',
    question:
      'What are the current state-of-the-art approaches to retrieval-augmented generation, and what measurable trade-offs distinguish them?',
  },
  {
    label: 'Regulatory scan',
    question:
      'What obligations do recent AI regulations place on providers of general-purpose models, and how do they differ by jurisdiction?',
  },
] as const;

export default function DashboardPage() {
  const [query, setQuery] = useState('');
  const stats = useDashboardStats();
  const runs = useResearchList(query ? { q: query } : {});

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Every research run you have started, with its sources, evidence and report."
        actions={
          <Button asChild>
            <Link href="/research/new">Start research</Link>
          </Button>
        }
      />

      <section aria-label="Summary" className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {stats.isPending ? (
          Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-24" />)
        ) : stats.isError ? (
          <div className="sm:col-span-2 lg:col-span-4">
            <ErrorState error={stats.error} onRetry={() => void stats.refetch()} />
          </div>
        ) : (
          <>
            <StatTile
              label="Research runs"
              value={formatCount(stats.data.total_runs)}
              hint={`${stats.data.completed_runs} completed · ${stats.data.running_runs} running`}
              icon={Activity}
            />
            <StatTile
              label="Sources gathered"
              value={formatCount(stats.data.total_sources)}
              hint={`${formatCount(stats.data.total_claims)} claims extracted`}
              icon={FileSearch}
            />
            <StatTile
              label="Total spend"
              value={formatCost(stats.data.total_cost_usd)}
              hint="Across all runs"
              icon={CircleDollarSign}
            />
            <StatTile
              label="Median runtime"
              value={formatDuration(stats.data.median_runtime_seconds)}
              hint={
                stats.data.median_runtime_seconds === null
                  ? 'Too few completed runs to measure'
                  : 'Completed runs only'
              }
              icon={Zap}
            />
          </>
        )}
      </section>

      <section aria-label="Quick start" className="mb-6">
        <Card>
          <CardHeader>
            <CardTitle>Start from a template</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {QUICK_STARTS.map((item) => (
              <Button key={item.label} asChild variant="outline" size="sm">
                <Link href={`/research/new?q=${encodeURIComponent(item.question)}`}>
                  {item.label}
                </Link>
              </Button>
            ))}
          </CardContent>
        </Card>
      </section>

      <section aria-label="Research history">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold">Research history</h2>
          <div className="relative w-full max-w-xs">
            <Search
              className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <Input
              type="search"
              placeholder="Search runs"
              aria-label="Search research runs"
              className="pl-8"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
        </div>

        <Card>
          {runs.isPending ? (
            <div className="flex flex-col gap-2 p-4">
              {Array.from({ length: 4 }).map((_, index) => (
                <Skeleton key={index} className="h-14" />
              ))}
            </div>
          ) : runs.isError ? (
            <div className="p-4">
              <ErrorState error={runs.error} onRetry={() => void runs.refetch()} />
            </div>
          ) : runs.data.items.length === 0 ? (
            <div className="p-4">
              <EmptyState
                icon={Quote}
                title={query ? 'No runs match that search' : 'No research yet'}
                description={
                  query
                    ? 'Try a different term, or clear the search to see every run.'
                    : 'Start a research run and its sources, evidence and report will appear here.'
                }
                action={
                  query ? null : (
                    <Button asChild size="sm">
                      <Link href="/research/new">Start research</Link>
                    </Button>
                  )
                }
              />
            </div>
          ) : (
            <RunTable runs={runs.data.items} />
          )}
        </Card>
      </section>
    </>
  );
}
