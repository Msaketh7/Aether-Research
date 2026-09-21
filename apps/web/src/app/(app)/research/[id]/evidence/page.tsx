'use client';

import { CLAIM_STATUSES, type ClaimStatus } from '@aether/shared-types';
import { GitCompareArrows, Quote } from 'lucide-react';
import { useState } from 'react';
import { EmptyState } from '@/components/common/empty-state';
import { ErrorState } from '@/components/common/error-state';
import { ClaimCard } from '@/components/research/claim-card';
import { ContradictionCard } from '@/components/research/contradiction-card';
import { useRunContext } from '@/components/research/run-context';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useResearchEvidence, useResearchSources } from '@/lib/api/queries';

/**
 * Claims, their evidence, and detected contradictions (FR-6, FR-7).
 *
 * Contradictions are placed above the claim list, not in a footnote: a
 * disagreement between sources is the most decision-relevant thing on the page.
 */
export default function RunEvidencePage() {
  const { runId } = useRunContext();
  const [status, setStatus] = useState<ClaimStatus | null>(null);
  const evidence = useResearchEvidence(runId, status ?? undefined);
  const sources = useResearchSources(runId);

  const sourcesById = new Map((sources.data?.sources ?? []).map((source) => [source.id, source]));
  const contradictions = evidence.data?.contradictions ?? [];

  return (
    <div className="flex flex-col gap-6">
      {contradictions.length > 0 ? (
        <section aria-label="Contradictions" className="flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <GitCompareArrows className="size-4 text-warning-strong" aria-hidden />
            <h2 className="text-sm font-semibold">
              {contradictions.length} contradiction{contradictions.length === 1 ? '' : 's'}
            </h2>
            <span className="text-xs text-muted-foreground">
              Recorded as disagreements, never silently resolved
            </span>
          </div>
          {contradictions.map((contradiction) => (
            <ContradictionCard
              key={contradiction.id}
              contradiction={contradiction}
              sourcesById={sourcesById}
            />
          ))}
        </section>
      ) : null}

      <section aria-label="Claims" className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="mr-2 text-sm font-semibold">Claims</h2>
          <Button
            variant={status === null ? 'secondary' : 'ghost'}
            size="sm"
            onClick={() => setStatus(null)}
          >
            All
          </Button>
          {CLAIM_STATUSES.map((claimStatus) => (
            <Button
              key={claimStatus}
              variant={status === claimStatus ? 'secondary' : 'ghost'}
              size="sm"
              className="capitalize"
              onClick={() => setStatus(claimStatus)}
              data-testid={`filter-${claimStatus}`}
            >
              {claimStatus}
            </Button>
          ))}
        </div>

        {evidence.isPending ? (
          <div className="flex flex-col gap-3">
            {Array.from({ length: 4 }).map((_, index) => (
              <Skeleton key={index} className="h-48" />
            ))}
          </div>
        ) : evidence.isError ? (
          <ErrorState error={evidence.error} onRetry={() => void evidence.refetch()} />
        ) : evidence.data.claims.length === 0 ? (
          <EmptyState
            icon={Quote}
            title={status ? `No ${status} claims` : 'No claims yet'}
            description={
              status
                ? 'No claim in this run currently has that status. Clear the filter to see the rest.'
                : 'Claims appear here as evidence is extracted from the sources.'
            }
            action={
              status ? (
                <Button variant="outline" size="sm" onClick={() => setStatus(null)}>
                  Clear filter
                </Button>
              ) : null
            }
          />
        ) : (
          evidence.data.claims.map((claim) => (
            <ClaimCard key={claim.id} claim={claim} sourcesById={sourcesById} />
          ))
        )}
      </section>
    </div>
  );
}
