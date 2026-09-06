import type { Contradiction, Source } from '@aether/shared-types';
import { GitCompareArrows } from 'lucide-react';
import { SourceLink } from '@/components/common/external-link';
import { Card } from '@/components/ui/card';
import { formatDateTime } from '@/lib/format';
import { ResolutionBadge } from './badges';

/**
 * A detected disagreement between sources (FR-7).
 *
 * Both values are shown side by side with equal prominence, and the system's
 * explanation is labelled as a hypothesis. Silently picking a winner is the
 * failure mode this component exists to prevent.
 */
export function ContradictionCard({
  contradiction,
  sourcesById,
}: {
  contradiction: Contradiction;
  sourcesById: Map<string, Source>;
}) {
  const sourceA = sourcesById.get(contradiction.source_a_id);
  const sourceB = sourcesById.get(contradiction.source_b_id);

  return (
    <Card className="border-warning/40 p-4" data-testid="contradiction-card">
      <div className="flex flex-wrap items-center gap-2">
        <GitCompareArrows className="size-4 text-warning" aria-hidden />
        <span className="font-mono text-xs text-muted-foreground">
          {contradiction.normalized_key}
        </span>
        <span className="ml-auto">
          <ResolutionBadge resolution={contradiction.resolution} />
        </span>
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        {[
          { label: 'Source A', value: contradiction.value_a, source: sourceA },
          { label: 'Source B', value: contradiction.value_b, source: sourceB },
        ].map((side) => (
          <div key={side.label} className="rounded-md border border-border bg-muted/40 p-3">
            <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              {side.label}
            </p>
            <p className="mt-1 text-sm leading-snug">{side.value}</p>
            <p className="mt-2 text-xs text-muted-foreground">
              {side.source ? (
                <SourceLink href={side.source.url}>{side.source.publisher}</SourceLink>
              ) : (
                'Source unavailable'
              )}
            </p>
          </div>
        ))}
      </div>

      <div className="mt-3 rounded-md bg-warning/8 p-3">
        <p className="text-[11px] font-medium uppercase tracking-wide text-warning">
          Likely reason (hypothesis)
        </p>
        <p className="mt-1 text-sm leading-relaxed">{contradiction.likely_reason}</p>
      </div>

      <p className="mt-2.5 text-xs text-muted-foreground">
        Detected {formatDateTime(contradiction.detected_at)}
        {contradiction.resolved_by ? ` · resolved by ${contradiction.resolved_by}` : ''}
      </p>
    </Card>
  );
}
