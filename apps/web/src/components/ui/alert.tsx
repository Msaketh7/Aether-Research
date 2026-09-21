import { cva, type VariantProps } from 'class-variance-authority';
import type * as React from 'react';
import { cn } from '@/lib/utils';

const alertVariants = cva(
  [
    'relative flex w-full gap-3 rounded-xl border p-4 text-sm leading-relaxed',
    'reveal',
    '[&>svg]:size-4 [&>svg]:shrink-0 [&>svg]:translate-y-0.5',
  ],
  {
    variants: {
      variant: {
        default: 'border-border bg-card text-card-foreground',
        info: 'border-info/30 bg-info/8 text-foreground [&>svg]:text-info-strong',
        warning: 'border-warning/35 bg-warning/10 text-foreground [&>svg]:text-warning-strong',
        danger:
          'border-destructive/35 bg-destructive/8 text-foreground [&>svg]:text-destructive-strong',
      },
    },
    defaultVariants: { variant: 'default' },
  },
);

export interface AlertProps
  extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof alertVariants> {}

export function Alert({ className, variant, ...props }: AlertProps) {
  return <div role="alert" className={cn(alertVariants({ variant }), className)} {...props} />;
}

export function AlertTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h5 className={cn('font-medium leading-none', className)} {...props} />;
}

export function AlertDescription({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('text-sm text-muted-foreground', className)} {...props} />;
}
