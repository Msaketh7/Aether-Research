import * as React from 'react';
import { cn } from '@/lib/utils';

export function Input({ className, type, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      type={type}
      className={cn(
        'flex h-9 w-full rounded-lg border border-input bg-card/60 px-3 py-1 text-sm shadow-e1',
        'transition-[border-color,box-shadow,background-color] duration-[var(--duration-fast)] ease-[var(--ease-out-soft)]',
        'hover:border-primary/35',
        'focus-visible:border-primary/60 focus-visible:shadow-glow focus-visible:outline-none',
        'placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50',
        'aria-invalid:border-destructive aria-invalid:focus-visible:shadow-[0_0_0_1px_var(--destructive)]',
        className,
      )}
      {...props}
    />
  );
}
