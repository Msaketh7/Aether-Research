import type { ReactNode } from 'react';
import { AppShell } from '@/components/layout/app-shell';
import { RequireSession } from '@/components/layout/require-session';

/**
 * Chrome for every signed-in page.
 *
 * `RequireSession` sends a signed-out visitor to /login (Phase 20). It is a
 * redirect rather than the boundary: the API refuses every request without a
 * session, so this route group is where that refusal is turned into somewhere
 * to go. See the component for why the check cannot live in middleware.
 */
export default function AppLayout({ children }: { children: ReactNode }) {
  return (
    <AppShell>
      <RequireSession>{children}</RequireSession>
    </AppShell>
  );
}
