'use client';

import * as ProgressPrimitive from '@radix-ui/react-progress';
import type * as React from 'react';
import { cn } from '@/lib/utils';

/**
 * Determinate progress, with a sheen travelling along the filled part.
 *
 * The bar itself moves with `translateX` on a full-width indicator rather than
 * animating `width`, so advancing it never triggers layout. `indeterminate`
 * is a separate prop, not `value={null}`: "we do not know how far along this
 * is" and "this is at zero" must never render the same way.
 */
export function Progress({
  className,
  value = 0,
  indeterminate = false,
  ...props
}: React.ComponentProps<typeof ProgressPrimitive.Root> & { indeterminate?: boolean }) {
  const pct = Math.max(0, Math.min(100, value ?? 0));

  return (
    <ProgressPrimitive.Root
      className={cn(
        'relative h-1.5 w-full overflow-hidden rounded-full bg-muted',

        className,
      )}
      value={indeterminate ? null : value}
      {...props}
    >
      {indeterminate ? (
        <div
          className="h-full w-full origin-left rounded-full bg-primary"
          style={{ animation: 'indeterminate 1.4s var(--ease-out-soft) infinite' }}
          data-motion="loop"
          aria-hidden
        />
      ) : (
        <ProgressPrimitive.Indicator
          className={cn(
            'relative h-full w-full flex-1 overflow-hidden rounded-full bg-primary',
            'transition-transform duration-[var(--duration-slower)] ease-[var(--ease-out-soft)]',
          )}
          style={{ transform: `translateX(-${100 - pct}%)` }}
        >
          {/* The sheen says "still moving" while the value itself is static. */}
          <span
            className="absolute inset-y-0 w-1/3 bg-linear-to-r from-transparent via-white/35 to-transparent"
            style={{ animation: 'sweep 2.6s linear infinite' }}
            data-motion="loop"
            aria-hidden
          />
        </ProgressPrimitive.Indicator>
      )}
    </ProgressPrimitive.Root>
  );
}
