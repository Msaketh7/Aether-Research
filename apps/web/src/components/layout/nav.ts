import { BarChart3, LayoutDashboard, Settings, type LucideIcon } from 'lucide-react';

/**
 * The primary destinations, in one place.
 *
 * Both the desktop rail and the mobile drawer render from this list, so the two
 * cannot drift into offering different navigation - which is the usual way a
 * responsive app ends up with a destination reachable on one size only.
 */
export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  description: string;
}

export const NAV_ITEMS: readonly NavItem[] = [
  {
    href: '/dashboard',
    label: 'Dashboard',
    icon: LayoutDashboard,
    description: 'Every run, with its sources and report',
  },
  {
    href: '/evaluations',
    label: 'Evaluations',
    icon: BarChart3,
    description: 'Benchmark results and system metrics',
  },
  {
    href: '/settings',
    label: 'Settings',
    icon: Settings,
    description: 'Account, sessions and preferences',
  },
] as const;

/**
 * A nav item is current for its own page and for anything nested under it -
 * except the root, which every path is nested under and which therefore has to
 * match exactly or it would light up on every screen in the product.
 */
export function isCurrent(pathname: string, href: string): boolean {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}
