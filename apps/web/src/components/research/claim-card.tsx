import type { ClaimWithEvidence, Evidence, Source } from '@aether/shared-types';
import { ConfidenceMeter } from '@/components/common/confidence-meter';
import { SourceLink } from '@/components/common/external-link';
import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';
import { ClaimStatusBadge, StanceLabel } from './badges';

/**
 * A claim with its evidence (FR-6).
 *
 * Refuting evidence is rendered with the same weight as supporting evidence.
 * Burying disagreement would defeat the purpose of extracting it.
 */

function EvidenceRow({ evidence, source }: { evidence: Evidence; source: Source | undefined }) {
  const refutes = evidence.stance === 'refutes';

  return (
    <li
      className={cn('border-l-2 py-1.5 pl-3', refutes ? 'border-refutes' : 'border-supports')}
      data-testid="evidence-row"
    >
      <div className="flex flex-wrap items-center gap-2">
        <StanceLabel stance={evidence.stance} />
        <span className="font-mono text-[11px] tabular-nums text-muted-foreground">
          {Math.round(evidence.confidence * 100)}%
        </span>
        <span className="text-xs text-muted-foreground">
          chars {evidence.span_start}–{evidence.span_end}
        </span>
      </div>

      <blockquote className="mt-1 text-sm italic leading-relaxed text-foreground">
        “{evidence.span_text}”
      </blockquote>

      <p className="mt-1 text-xs text-muted-foreground">
        {source ? (
          <SourceLink href={source.url}>
            {source.title} — {source.publisher}
          </SourceLink>
        ) : (
          'Source unavailable'
        )}
        <span className="ml-2 opacity-70">via {evidence.extractor_model}</span>
      </p>
    </li>
  );
}

export function ClaimCard({
  claim,
  sourcesById,
}: {
  claim: ClaimWithEvidence;
  sourcesById: Map<string, Source>;
}) {
  const uncorroborated = claim.corroboration_count <= 1;

  return (
    <Card className="p-4" data-testid="claim-card" data-claim-status={claim.status}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="min-w-0 flex-1 text-sm font-medium leading-snug">{claim.text}</p>
        <ClaimStatusBadge status={claim.status} />
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-2">
        <ConfidenceMeter value={claim.confidence} />
        <span className="text-xs text-muted-foreground">
          {claim.corroboration_count} source{claim.corroboration_count === 1 ? '' : 's'}
          {uncorroborated ? ' — uncorroborated' : ''}
        </span>
        {claim.task_external_id ? (
          <span className="font-mono text-[11px] text-muted-foreground">
            {claim.task_external_id}
          </span>
        ) : null}
        <span className="font-mono text-[11px] text-muted-foreground/70">
          {claim.normalized_key}
        </span>
      </div>

      <ul className="mt-3 flex flex-col gap-2.5">
        {claim.supporting.map((evidence) => (
          <EvidenceRow
            key={evidence.id}
            evidence={evidence}
            source={sourcesById.get(evidence.source_id)}
          />
        ))}
        {claim.refuting.map((evidence) => (
          <EvidenceRow
            key={evidence.id}
            evidence={evidence}
            source={sourcesById.get(evidence.source_id)}
          />
        ))}
      </ul>
    </Card>
  );
}
