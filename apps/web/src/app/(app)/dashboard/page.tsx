'use client';

import { Activity, CircleDollarSign, FileSearch, Quote, Search, Zap } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';
import { EmptyState } from '@/components/common/empty-state';
import { ErrorState } from '@/components/common/error-state';
import { PageHeader } from '@/components/common/page-header';
import { StatTile } from '@/components/common/stat-tile';
import { RunList } from '@/components/research/run-list';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { formatCost, formatCount, formatDuration } from '@/lib/format';
import { useDashboardStats, useResearchList } from '@/lib/api/queries';

/**
 * The record of what has been asked: totals, and every run with its findings.
 *
 * The question box lives on the home screen, not here. Two places to start a
 * run is one too many, and a dashboard that also asks you a question is not a
 * dashboard.
 */
export default function DashboardPage() {
  const [query, setQuery] = useState('');
  const stats = useDashboardStats();
  const runs = useResearchList(query ? { q: query } : {});

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title="Dashboard"
        description="Every research run you have started, with its sources, evidence and report."
        actions={
          <Button asChild>
            <Link href="/">Ask a question</Link>
          </Button>
        }
      />

      <section
        aria-label="Summary"
        className="stagger mb-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
      >
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

      <section aria-label="Research history">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold">Research history</h2>
          <div className="relative w-full max-w-xs">
            <Search
              className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <Input
              type="search"
              placeholder="Search runs"
              aria-label="Search research runs"
              className="pl-9"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
        </div>

        {runs.isPending ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-28" />
            ))}
          </div>
        ) : runs.isError ? (
          <ErrorState error={runs.error} onRetry={() => void runs.refetch()} />
        ) : runs.data.items.length === 0 ? (
          <EmptyState
            icon={Quote}
            title={query ? 'No runs match that search' : 'No research yet'}
            description={
              query
                ? 'Try a different term, or clear the search to see every run.'
                : 'Ask a question and its sources, evidence and report will appear here.'
            }
            action={
              query ? (
                <Button size="sm" variant="outline" onClick={() => setQuery('')}>
                  Clear search
                </Button>
              ) : (
                <Button asChild size="sm">
                  <Link href="/">Ask a question</Link>
                </Button>
              )
            }
          />
        ) : (
          <RunList runs={runs.data.items} />
        )}
      </section>
    </div>
  );
}
