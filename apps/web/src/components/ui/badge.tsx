import { cva, type VariantProps } from 'class-variance-authority';
import type * as React from 'react';
import { cn } from '@/lib/utils';

const badgeVariants = cva(
  [
    'inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap',
    'transition-colors duration-[var(--duration-fast)] ease-[var(--ease-out-soft)]',
  ],
  {
    variants: {
      variant: {
        default: 'border-transparent bg-secondary text-secondary-foreground',
        outline: 'border-border text-foreground',
        primary: 'border-primary/25 bg-primary/12 text-primary-strong',
        success: 'border-success/25 bg-success/15 text-success-strong',
        warning: 'border-warning/30 bg-warning/18 text-warning-strong',
        danger: 'border-destructive/25 bg-destructive/15 text-destructive-strong',
        info: 'border-info/25 bg-info/15 text-info-strong',
      },
    },
    defaultVariants: { variant: 'default' },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { badgeVariants };
