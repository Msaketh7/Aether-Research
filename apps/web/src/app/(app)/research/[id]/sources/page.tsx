'use client';

import { SOURCE_TYPES, type SourceType } from '@aether/shared-types';
import { FileSearch } from 'lucide-react';
import { useState } from 'react';
import { EmptyState } from '@/components/common/empty-state';
import { ErrorState } from '@/components/common/error-state';
import { useRunContext } from '@/components/research/run-context';
import { SourceCard } from '@/components/research/source-card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useResearchSources } from '@/lib/api/queries';

const TYPE_LABELS: Record<SourceType, string> = {
  web: 'Web',
  sec: 'SEC',
  arxiv: 'arXiv',
  github: 'GitHub',
  upload: 'Uploads',
};

/** Discovered sources, with duplicate clusters surfaced rather than hidden. */
export default function RunSourcesPage() {
  const { runId, run } = useRunContext();
  const [type, setType] = useState<SourceType | null>(null);
  const sources = useResearchSources(runId, type ?? undefined);

  const clustersBySource = new Map(
    (sources.data?.clusters ?? []).map((cluster) => [cluster.primary_source_id, cluster]),
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant={type === null ? 'secondary' : 'ghost'}
          size="sm"
          onClick={() => setType(null)}
        >
          All
          {sources.data ? (
            <span className="ml-1 font-mono text-xs text-muted-foreground">
              {run?.source_count ?? sources.data.total}
            </span>
          ) : null}
        </Button>
        {SOURCE_TYPES.map((sourceType) => (
          <Button
            key={sourceType}
            variant={type === sourceType ? 'secondary' : 'ghost'}
            size="sm"
            onClick={() => setType(sourceType)}
            data-testid={`filter-${sourceType}`}
          >
            {TYPE_LABELS[sourceType]}
          </Button>
        ))}
      </div>

      {sources.isPending ? (
        <div className="flex flex-col gap-3">
          {Array.from({ length: 5 }).map((_, index) => (
            <Skeleton key={index} className="h-40" />
          ))}
        </div>
      ) : sources.isError ? (
        <ErrorState error={sources.error} onRetry={() => void sources.refetch()} />
      ) : sources.data.sources.length === 0 ? (
        <EmptyState
          icon={FileSearch}
          title={type ? `No ${TYPE_LABELS[type].toLowerCase()} sources` : 'No sources yet'}
          description={
            type
              ? 'This run has not discovered a source of that type. Clear the filter to see everything it did find.'
              : 'Sources appear here as the researchers discover them.'
          }
          action={
            type ? (
              <Button variant="outline" size="sm" onClick={() => setType(null)}>
                Clear filter
              </Button>
            ) : null
          }
        />
      ) : (
        <>
          <p className="text-xs text-muted-foreground">
            {sources.data.total} source{sources.data.total === 1 ? '' : 's'}
            {sources.data.clusters.length > 0
              ? ` · ${sources.data.clusters.length} duplicate cluster${sources.data.clusters.length === 1 ? '' : 's'} collapsed`
              : ''}
          </p>
          <div className="flex flex-col gap-3">
            {sources.data.sources
              .filter(
                (source) =>
                  // Hide sources that are the *duplicate* member of a cluster;
                  // the primary card reports how many were collapsed into it.
                  !sources.data.clusters.some((cluster) =>
                    cluster.duplicate_source_ids.includes(source.id),
                  ),
              )
              .map((source) => (
                <SourceCard
                  key={source.id}
                  source={source}
                  cluster={clustersBySource.get(source.id)}
                />
              ))}
          </div>
        </>
      )}
    </div>
  );
}
