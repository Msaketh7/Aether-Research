import type * as React from 'react';
import { cn } from '@/lib/utils';

export function Textarea({
  className,
  ...props
}: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={cn(
        'flex min-h-24 w-full resize-y rounded-lg border border-input bg-card/60 px-3 py-2 text-sm shadow-e1',
        'transition-[border-color,box-shadow,background-color] duration-[var(--duration-fast)] ease-[var(--ease-out-soft)]',
        'hover:border-primary/35',
        'focus-visible:border-primary/60 focus-visible:shadow-glow focus-visible:outline-none',
        'placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50',
        'aria-invalid:border-destructive',
        className,
      )}
      {...props}
    />
  );
}
