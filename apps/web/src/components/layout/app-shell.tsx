import type { ReactNode } from 'react';
import { DemoBanner } from './demo-banner';
import { Sidebar } from './sidebar';
import { Topbar } from './topbar';

/**
 * Persistent chrome for every signed-in page.
 *
 * The skip link is first in the DOM so a keyboard user is not tabbed through
 * the whole rail on every navigation. The mesh is a fixed decorative layer
 * behind everything, not a background on `main`: as a background it would
 * repeat down a long report.
 */
export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="relative flex min-h-dvh">
      <div className="mesh pointer-events-none fixed inset-0 -z-10" aria-hidden />

      <a
        href="#main"
        className="sr-only rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow-e2 focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[60]"
      >
        Skip to main content
      </a>

      <Sidebar />

      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <DemoBanner />
        <main id="main" tabIndex={-1} className="flex-1 px-4 py-6 sm:px-6 lg:px-8">
          {children}
        </main>
      </div>
    </div>
  );
}
