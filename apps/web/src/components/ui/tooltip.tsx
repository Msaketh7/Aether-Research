'use client';

import * as TooltipPrimitive from '@radix-ui/react-tooltip';
import type * as React from 'react';
import { cn } from '@/lib/utils';

export const TooltipProvider = TooltipPrimitive.Provider;
export const Tooltip = TooltipPrimitive.Root;
export const TooltipTrigger = TooltipPrimitive.Trigger;

export function TooltipContent({
  className,
  sideOffset = 6,
  ...props
}: React.ComponentProps<typeof TooltipPrimitive.Content>) {
  return (
    <TooltipPrimitive.Portal>
      <TooltipPrimitive.Content
        sideOffset={sideOffset}
        className={cn(
          'z-50 max-w-xs rounded-lg border border-border bg-popover px-3 py-1.5 text-xs leading-relaxed text-popover-foreground shadow-e2',
          'data-[state=open]:animate-[scale-in_var(--duration-base)_var(--ease-out-quick)_both]',
          'data-[state=closed]:animate-[fade-out_var(--duration-fast)_var(--ease-in-quick)_both]',
          'data-[side=bottom]:origin-top data-[side=top]:origin-bottom',
          'data-[side=left]:origin-right data-[side=right]:origin-left',
          className,
        )}
        {...props}
      />
    </TooltipPrimitive.Portal>
  );
}
