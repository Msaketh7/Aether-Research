'use client';

import { ArrowRight, Check, LoaderCircle } from 'lucide-react';
import { useRef, type ButtonHTMLAttributes, type PointerEvent, type ReactNode } from 'react';
import { cn } from '@/lib/utils';

/**
 * The glass panel the sign-in and registration forms sit on.
 *
 * Its edge carries a travelling arc of light (`.auth-card::before`) and its
 * face a soft spotlight that follows the pointer (`::after`), both drawn in
 * `auth.css`. The spotlight's position is written straight to two custom
 * properties rather than through React state: a pointer moves sixty times a
 * second, and re-rendering a form on every one of them would drop keystrokes
 * on a slow machine. The rect is measured once on entry, not on every move,
 * so the handler never forces a layout.
 *
 * The heading lives here, outside any Suspense boundary the form needs, so
 * the page's one `h1` is in the server-rendered HTML.
 */
export function AuthCard({
  title,
  description,
  children,
  className,
}: {
  title: string;
  description: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const rect = useRef<DOMRect | null>(null);

  const onPointerEnter = (event: PointerEvent<HTMLDivElement>) => {
    rect.current = event.currentTarget.getBoundingClientRect();
  };

  const onPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    const bounds = rect.current;
    if (!bounds || event.pointerType !== 'mouse') return;
    event.currentTarget.style.setProperty('--mx', `${event.clientX - bounds.left}px`);
    event.currentTarget.style.setProperty('--my', `${event.clientY - bounds.top}px`);
  };

  return (
    <div
      data-auth-card
      onPointerEnter={onPointerEnter}
      onPointerMove={onPointerMove}
      className={cn('auth-card auth-enter p-6 [--enter-delay:180ms] sm:p-8', className)}
    >
      <header className="mb-7 flex flex-col gap-2">
        <h1 className="font-display text-[1.875rem] leading-tight font-medium text-foreground">
          {title}
        </h1>
        <p className="text-sm leading-relaxed text-muted-foreground">{description}</p>
      </header>
      {children}
    </div>
  );
}

/**
 * The primary action. A gradient that slides on hover, a sheen that crosses
 * it, and an arrow that leans toward where the button goes.
 *
 * Three states, each with its own glyph, so the button itself says what is
 * happening: idle (arrow), working (spinner, and disabled - a second click
 * would be a second request), and done (a check, held while the router
 * navigates, so success is seen rather than inferred from a page change).
 */
export function AuthSubmit({
  pending,
  done,
  children,
  pendingLabel,
  doneLabel,
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  pending: boolean;
  done: boolean;
  pendingLabel: string;
  doneLabel: string;
}) {
  return (
    <button
      type="submit"
      disabled={pending || done}
      aria-busy={pending}
      // Styled per state, not per `disabled`: a button waiting on a request, a
      // button that has succeeded and a button with nothing to send yet are
      // all disabled, and only the last should look unavailable.
      data-state={pending ? 'pending' : done ? 'done' : 'idle'}
      className={cn('auth-cta group', className)}
      {...props}
    >
      <span className="relative z-[1] inline-flex items-center gap-2">
        {pending ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : null}
        {done ? <Check className="auth-pop size-4" aria-hidden /> : null}
        <span>{pending ? pendingLabel : done ? doneLabel : children}</span>
        {!pending && !done ? (
          <ArrowRight
            aria-hidden
            className="size-4 transition-transform duration-[var(--duration-base)] ease-[var(--ease-spring)] group-hover:translate-x-1"
          />
        ) : null}
      </span>
    </button>
  );
}
