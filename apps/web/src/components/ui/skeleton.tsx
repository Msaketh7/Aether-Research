import type * as React from 'react';
import { cn } from '@/lib/utils';

/** Placeholder used while a query is loading. Never used to fake content. */
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('animate-pulse rounded-md bg-muted', className)} {...props} />;
}
