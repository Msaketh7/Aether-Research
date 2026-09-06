'use client';

import { BarChart3, FlaskConical, LayoutDashboard, Plus, Settings } from 'lucide-react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { APP_NAME } from '@/lib/api/config';
import { cn } from '@/lib/utils';

const NAV = [
  { href: '/dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/evaluations', label: 'Evaluations', icon: BarChart3 },
  { href: '/settings', label: 'Settings', icon: Settings },
] as const;

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="hidden w-60 shrink-0 flex-col border-r border-sidebar-border bg-sidebar lg:flex">
      <div className="flex h-14 items-center gap-2 border-b border-sidebar-border px-5">
        <FlaskConical className="size-4 text-primary" aria-hidden />
        <span className="text-sm font-semibold tracking-tight">{APP_NAME}</span>
      </div>

      <div className="p-3">
        <Button asChild className="w-full justify-start" size="sm">
          <Link href="/research/new">
            <Plus aria-hidden />
            New research
          </Link>
        </Button>
      </div>

      <nav className="flex flex-1 flex-col gap-0.5 px-3 pb-3" aria-label="Main">
        {NAV.map(({ href, label, icon: Icon }) => {
          const active = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              aria-current={active ? 'page' : undefined}
              className={cn(
                'flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors',
                active
                  ? 'bg-accent font-medium text-accent-foreground'
                  : 'text-sidebar-foreground hover:bg-accent/60',
              )}
            >
              <Icon className="size-4 shrink-0" aria-hidden />
              {label}
            </Link>
          );
        })}
      </nav>

      <div className="border-t border-sidebar-border px-5 py-3">
        <p className="text-[11px] leading-relaxed text-muted-foreground">
          Phase 1 prototype — frontend against the mock API.
        </p>
      </div>
    </aside>
  );
}
