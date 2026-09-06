import type { ReactNode } from 'react';
import { AppShell } from '@/components/layout/app-shell';

/**
 * Chrome for every signed-in page. Session enforcement is added here in Phase 2
 * via middleware; the route group exists now so that boundary has one home.
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  return <AppShell>{children}</AppShell>;
}
