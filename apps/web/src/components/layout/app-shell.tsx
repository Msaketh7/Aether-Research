import type { ReactNode } from 'react';
import { DemoBanner } from './demo-banner';
import { Sidebar } from './sidebar';
import { Topbar } from './topbar';

/** Persistent chrome for every signed-in page. */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-dvh">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <DemoBanner />
        <main className="flex-1 px-4 py-6 sm:px-6 lg:px-8">{children}</main>
      </div>
    </div>
  );
}
