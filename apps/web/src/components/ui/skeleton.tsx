import type * as React from 'react';
import { cn } from '@/lib/utils';

/**
 * Placeholder used while a query is loading. Never used to fake content.
 *
 * A sweep rather than a pulse: a block fading in and out at the same rate as a
 * disabled control reads as broken, whereas a travelling highlight reads as
 * work in progress. The sweep is one gradient on one element, so a page of
 * twenty skeletons still composites in a single frame.
 */
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('shimmer rounded-lg bg-muted', className)}
      data-motion="loop"
      aria-hidden
      {...props}
    />
  );
}
