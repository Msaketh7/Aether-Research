import type { Source, SourceCluster } from '@aether/shared-types';
import { Copy, Layers, ShieldCheck } from 'lucide-react';
import { SourceLink } from '@/components/common/external-link';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { formatDate, formatDateTime, formatPercent } from '@/lib/format';
import { SourceTypeBadge } from './badges';

/**
 * One discovered source (FR-5).
 *
 * Shows `accessed_at` next to `published_at` deliberately: when Aether read a
 * page and when the page was written are different facts, and a citation is
 * only meaningful with both.
 */
export function SourceCard({
  source,
  cluster,
}: {
  source: Source;
  cluster?: SourceCluster | undefined;
}) {
  const duplicates = cluster?.duplicate_source_ids.length ?? 0;

  return (
    <Card className="p-4" data-testid="source-card">
      <div className="flex flex-wrap items-center gap-2">
        <SourceTypeBadge type={source.source_type} />
        {source.credibility_metadata.is_primary ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge variant="info" className="cursor-help">
                <ShieldCheck className="size-3" aria-hidden />
                Primary
              </Badge>
            </TooltipTrigger>
            <TooltipContent>
              A primary source: the filing, paper, repository or first-party statement itself,
              rather than commentary about it.
            </TooltipContent>
          </Tooltip>
        ) : null}
        {duplicates > 0 ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <Badge variant="warning" className="cursor-help">
                <Layers className="size-3" aria-hidden />
                {duplicates} duplicate{duplicates === 1 ? '' : 's'}
              </Badge>
            </TooltipTrigger>
            <TooltipContent>
              Near-duplicate sources were collapsed into this one so the same content is not counted
              twice as corroboration.
            </TooltipContent>
          </Tooltip>
        ) : null}
        {source.credibility_metadata.notes ? (
          <Badge variant="outline">{source.credibility_metadata.notes}</Badge>
        ) : null}

        <span className="ml-auto shrink-0 font-mono text-xs tabular-nums text-muted-foreground">
          relevance {formatPercent(source.relevance_score)}
        </span>
      </div>

      <h3 className="mt-2.5 text-sm font-medium leading-snug">
        <SourceLink href={source.url}>{source.title}</SourceLink>
      </h3>

      <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{source.excerpt}</p>

      <dl className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
        <div className="flex gap-1">
          <dt className="sr-only">Publisher</dt>
          <dd className="font-medium text-foreground">{source.publisher}</dd>
        </div>
        {source.author ? (
          <div className="flex gap-1">
            <dt>Author</dt>
            <dd>{source.author}</dd>
          </div>
        ) : null}
        <div className="flex gap-1">
          <dt>Published</dt>
          <dd>{formatDate(source.published_at)}</dd>
        </div>
        <div className="flex gap-1">
          <dt>Accessed</dt>
          <dd>{formatDateTime(source.accessed_at)}</dd>
        </div>
        <div className="flex gap-1">
          <dt>Claims</dt>
          <dd>{source.claim_count}</dd>
        </div>
        <Tooltip>
          <TooltipTrigger asChild>
            <div className="flex cursor-help items-center gap-1">
              <Copy className="size-3" aria-hidden />
              <dt className="sr-only">Content hash</dt>
              <dd className="font-mono">{source.content_hash.slice(0, 12)}</dd>
            </div>
          </TooltipTrigger>
          <TooltipContent>
            sha256 of the normalized content. Two sources with the same hash are exact duplicates.
          </TooltipContent>
        </Tooltip>
      </dl>
    </Card>
  );
}
