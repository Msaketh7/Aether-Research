'use client';

import * as PopoverPrimitive from '@radix-ui/react-popover';
import type * as React from 'react';
import { cn } from '@/lib/utils';

export const Popover = PopoverPrimitive.Root;
export const PopoverTrigger = PopoverPrimitive.Trigger;
export const PopoverAnchor = PopoverPrimitive.Anchor;

export function PopoverContent({
  className,
  align = 'center',
  sideOffset = 6,
  ...props
}: React.ComponentProps<typeof PopoverPrimitive.Content>) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Content
        align={align}
        sideOffset={sideOffset}
        className={cn(
          'z-50 w-80 rounded-xl border border-border bg-popover p-4 text-popover-foreground shadow-e3 outline-none',
          'data-[state=open]:animate-[scale-in_var(--duration-base)_var(--ease-out-quick)_both]',
          'data-[state=closed]:animate-[fade-out_var(--duration-fast)_var(--ease-in-quick)_both]',
          'data-[side=bottom]:origin-top data-[side=top]:origin-bottom',
          'data-[side=left]:origin-right data-[side=right]:origin-left',
          className,
        )}
        {...props}
      />
    </PopoverPrimitive.Portal>
  );
}
