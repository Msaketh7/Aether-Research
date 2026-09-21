'use client';

import { Menu, Plus, X } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { Brand } from './brand';
import { isCurrent, NAV_ITEMS } from './nav';
import { RecentRuns } from './recent-runs';

/**
 * Navigation below the `lg` breakpoint.
 *
 * Before this existed the rail was `hidden ... lg:flex` and nothing replaced
 * it, so on a phone Evaluations and Settings were reachable only by typing the
 * URL. A drawer rather than a bottom bar because the recent-run list is the
 * point of the rail and will not fit in a tab bar.
 *
 * Hand-built rather than pulled from a dialog library: the app has no dialog
 * primitive yet, and one drawer does not justify the dependency. What a dialog
 * library would give us is implemented explicitly below - Escape closes, focus
 * moves in and is restored on close, focus is trapped while open, the page
 * behind does not scroll, and the scrim is a labelled control rather than a
 * div you are expected to guess at.
 */
export function MobileNav() {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  const panelRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const close = useCallback(() => setOpen(false), []);

  useEffect(() => {
    if (!open) return;

    const previouslyFocused = document.activeElement as HTMLElement | null;
    const trigger = triggerRef.current;
    const { overflow } = document.body.style;
    document.body.style.overflow = 'hidden';

    panelRef.current?.querySelector<HTMLElement>('a, button')?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        close();
        return;
      }
      if (event.key !== 'Tab') return;

      const focusable = panelRef.current?.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])',
      );
      if (!focusable || focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;

      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = overflow;
      (previouslyFocused ?? trigger)?.focus?.();
    };
  }, [open, close]);

  return (
    <>
      <Button
        ref={triggerRef}
        variant="ghost"
        size="icon"
        className="lg:hidden"
        aria-label="Open navigation"
        aria-expanded={open}
        onClick={() => setOpen(true)}
      >
        <Menu aria-hidden />
      </Button>

      {open ? (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            type="button"
            aria-label="Close navigation"
            onClick={close}
            className="absolute inset-0 animate-[fade-in_var(--duration-fast)_var(--ease-out-soft)_both] bg-foreground/45 backdrop-blur-[2px]"
          />

          <div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label="Navigation"
            data-testid="mobile-nav"
            className={cn(
              'absolute inset-y-0 left-0 flex w-[17rem] max-w-[86vw] flex-col border-r border-sidebar-border bg-sidebar shadow-e3',
              'animate-[slide-in-left_var(--duration-base)_var(--ease-out-quick)_both]',
            )}
          >
            <div className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-sidebar-border px-4">
              <Brand />
              <Button variant="ghost" size="icon" aria-label="Close navigation" onClick={close}>
                <X aria-hidden />
              </Button>
            </div>

            <div className="p-3">
              <Button asChild className="w-full justify-start" size="sm">
                <Link
                  href="/"
                  onClick={close}
                  aria-current={isCurrent(pathname, '/') ? 'page' : undefined}
                >
                  <Plus aria-hidden />
                  New research
                </Link>
              </Button>
            </div>

            <nav className="flex flex-col gap-0.5 px-3" aria-label="Main">
              {NAV_ITEMS.map(({ href, label, icon: Icon, description }) => {
                const active = isCurrent(pathname, href);
                return (
                  <Link
                    key={href}
                    href={href}
                    onClick={close}
                    aria-current={active ? 'page' : undefined}
                    className={cn(
                      'flex items-start gap-3 rounded-lg px-2.5 py-2.5 transition-colors duration-[var(--duration-fast)]',
                      active
                        ? 'bg-accent text-accent-foreground'
                        : 'text-sidebar-foreground hover:bg-hover',
                    )}
                  >
                    <Icon className="mt-0.5 size-4 shrink-0" aria-hidden />
                    <span className="min-w-0">
                      <span className={cn('block text-sm', active && 'font-medium')}>{label}</span>
                      <span className="mt-0.5 block text-xs leading-snug text-muted-foreground">
                        {description}
                      </span>
                    </span>
                  </Link>
                );
              })}
            </nav>

            <div className="mt-4 flex min-h-0 flex-1 flex-col px-3 pb-4">
              <h2 className="px-2.5 pb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                Recent runs
              </h2>
              <div className="min-h-0 flex-1 overflow-y-auto">
                <RecentRuns onNavigate={close} />
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
