'use client';

import type { Citation } from '@aether/shared-types';
import { ShieldAlert } from 'lucide-react';
import { ConfidenceMeter } from '@/components/common/confidence-meter';
import { SourceLink } from '@/components/common/external-link';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { cn } from '@/lib/utils';

/**
 * An inline `[n]` marker.
 *
 * Clicking it shows the source *and the verbatim quote* the claim rests on -
 * the shortest possible path from a statement in the report to the evidence
 * behind it (FR-9). A marker with no resolving citation renders as a visible
 * warning rather than silently disappearing: an unresolvable citation is a bug
 * the reader deserves to see.
 */
export function CitationMarker({
  ordinal,
  citation,
}: {
  ordinal: number;
  citation: Citation | undefined;
}) {
  if (!citation) {
    return (
      <span
        className="ml-0.5 inline-flex items-center gap-0.5 align-super text-[10px] font-medium text-destructive-strong"
        title="This citation could not be resolved to evidence."
        data-testid="citation-unresolved"
      >
        <ShieldAlert className="size-3" aria-hidden />[{ordinal}]
      </span>
    );
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          data-testid="citation-marker"
          data-ordinal={ordinal}
          aria-label={`Citation ${ordinal}: ${citation.source_title}`}
          className={cn(
            'mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded-[5px] px-1 align-super',
            'bg-primary/12 font-mono text-[10px] font-medium text-primary',
            'transition-[background-color,transform,box-shadow] duration-[var(--duration-fast)] ease-[var(--ease-out-soft)]',
            'hover:-translate-y-px hover:bg-primary/22 hover:shadow-e1 active:translate-y-0',
            'data-[state=open]:bg-primary data-[state=open]:text-primary-foreground',
          )}
        >
          {ordinal}
        </button>
      </PopoverTrigger>
      <PopoverContent
        className="w-96 animate-[scale-in_var(--duration-base)_var(--ease-out-quick)_both]"
        data-testid="citation-popover"
      >
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Source [{ordinal}]
        </p>
        <p className="mt-1 text-sm font-medium leading-snug">
          <SourceLink href={citation.source_url}>{citation.source_title}</SourceLink>
        </p>
        <p className="mt-0.5 text-xs text-muted-foreground">{citation.source_publisher}</p>

        <blockquote className="mt-3 border-l-2 border-supports pl-3 text-sm italic leading-relaxed">
          “{citation.quote}”
        </blockquote>

        <div className="mt-3 flex items-center justify-between gap-2">
          <ConfidenceMeter value={citation.confidence} />
        </div>
      </PopoverContent>
    </Popover>
  );
}
