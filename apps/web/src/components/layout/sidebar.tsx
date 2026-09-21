'use client';

import { Plus } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { API_MODE } from '@/lib/api/config';
import { cn } from '@/lib/utils';
import { Brand } from './brand';
import { isCurrent, NAV_ITEMS } from './nav';
import { RecentRuns } from './recent-runs';

/**
 * The desktop navigation rail.
 *
 * The active item is marked by a bar that grows out of the left edge rather
 * than by colour alone: at a glance down a list of three, a filled background
 * and a hover background look alike, and the bar does not.
 */
export function Sidebar() {
  const pathname = usePathname();
  const home = isCurrent(pathname, '/');

  return (
    <aside className="sticky top-0 hidden h-dvh w-64 shrink-0 flex-col border-r border-sidebar-border bg-sidebar/70 backdrop-blur-xl lg:flex">
      <div className="flex h-14 shrink-0 items-center px-5">
        <Brand href="/" />
      </div>

      <div className="px-3 pb-3">
        <Button asChild className="group w-full justify-start" size="sm">
          {/* This is the rail's entry for the home screen, not just a shortcut,
              so it carries the current-page state the nav rows below carry. */}
          <Link href="/" aria-current={home ? 'page' : undefined}>
            <Plus className="group-hover:rotate-90" aria-hidden />
            New research
          </Link>
        </Button>
      </div>

      <nav className="flex flex-col gap-0.5 px-3" aria-label="Main">
        {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
          const active = isCurrent(pathname, href);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? 'page' : undefined}
              className={cn(
                'group relative flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm',
                'transition-[background-color,color] duration-[var(--duration-fast)] ease-[var(--ease-out-soft)]',
                active
                  ? 'bg-accent font-medium text-accent-foreground'
                  : 'text-sidebar-foreground hover:bg-hover hover:text-foreground',
              )}
            >
              <span
                className={cn(
                  'absolute left-0 top-1/2 w-0.5 -translate-y-1/2 rounded-full bg-primary',
                  'transition-[height] duration-[var(--duration-base)] ease-[var(--ease-spring)]',
                  active ? 'h-5' : 'h-0',
                )}
                aria-hidden
              />
              <Icon
                className={cn(
                  'size-4 shrink-0 transition-transform duration-[var(--duration-base)] ease-[var(--ease-spring)]',
                  !active && 'group-hover:scale-110',
                )}
                aria-hidden
              />
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="mt-5 flex min-h-0 flex-1 flex-col px-3">
        <h2 className="px-2.5 pb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Recent runs
        </h2>
        <div className="-mr-1 min-h-0 flex-1 overflow-y-auto pb-3 pr-1">
          <RecentRuns />
        </div>
      </div>

      <div className="shrink-0 border-t border-sidebar-border px-5 py-3">
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          {API_MODE === 'mock'
            ? 'Prototype: the frontend running against the mock API.'
            : 'Connected to the Aether API. No research worker is running yet, so runs stay queued.'}
        </p>
      </div>
    </aside>
  );
}
