import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import * as React from 'react';
import { cn } from '@/lib/utils';

/**
 * Press feedback is a scale, not a colour change.
 *
 * A button that only changes colour on `:active` gives no feedback at all on a
 * touch screen, where the finger covers the button. `active:scale-[0.97]` is
 * felt rather than seen, and because it is a transform it cannot reflow the
 * row the button sits in.
 */
const buttonVariants = cva(
  [
    'relative inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-medium',
    'transition-[background-color,border-color,color,box-shadow,transform,opacity]',
    'duration-[var(--duration-fast)] ease-[var(--ease-out-soft)]',
    'active:scale-[0.97] active:duration-[var(--duration-instant)]',
    'disabled:pointer-events-none disabled:opacity-50',
    '[&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0',
    '[&_svg]:transition-transform [&_svg]:duration-[var(--duration-base)]',
  ],
  {
    variants: {
      variant: {
        default:
          'bg-primary text-primary-foreground shadow-e1 hover:shadow-glow hover:brightness-110',
        secondary: 'bg-secondary text-secondary-foreground hover:bg-accent hover:shadow-e1',
        outline:
          'border border-border bg-card/60 shadow-e1 hover:border-primary/45 hover:bg-accent hover:text-accent-foreground hover:shadow-e2',
        ghost: 'hover:bg-accent hover:text-accent-foreground',
        destructive:
          'bg-destructive text-destructive-foreground shadow-e1 hover:brightness-110 hover:shadow-e2',
        link: 'text-primary underline-offset-4 hover:underline active:scale-100',
      },
      size: {
        sm: 'h-8 px-3 text-xs',
        default: 'h-9 px-4 py-2',
        lg: 'h-11 px-6 text-base',
        icon: 'h-9 w-9',
      },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
);

export interface ButtonProps
  extends React.ComponentProps<'button'>, VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export function Button({ className, variant, size, asChild = false, ...props }: ButtonProps) {
  const Comp = asChild ? Slot : 'button';
  return <Comp className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}

export { buttonVariants };
