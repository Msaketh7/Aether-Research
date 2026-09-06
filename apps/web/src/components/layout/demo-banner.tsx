import { Info } from 'lucide-react';
import { API_MODE } from '@/lib/api/config';

/**
 * Honesty control, not decoration.
 *
 * While the app runs against the mock API every figure on screen is fixture
 * data. This banner states that on every page so a screenshot can never be
 * mistaken for real research output. It disappears automatically once
 * NEXT_PUBLIC_API_MODE=live.
 */
export function DemoBanner() {
  if (API_MODE !== 'mock') return null;

  return (
    <div
      className="flex items-start gap-2 border-b border-warning/35 bg-warning/10 px-4 py-2 text-xs text-foreground sm:px-6"
      data-testid="demo-banner"
    >
      <Info className="mt-0.5 size-3.5 shrink-0 text-warning" aria-hidden />
      <p>
        <span className="font-medium">Demo data.</span> This build runs against the mock API. Every
        run, source, claim, report and metric shown is synthetic fixture data for interface
        development — not real research output and not a measured benchmark.
      </p>
    </div>
  );
}
