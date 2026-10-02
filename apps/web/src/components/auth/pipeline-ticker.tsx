'use client';

import { RESEARCH_STAGES } from '@aether/shared-types';
import { useEffect, useState } from 'react';
import { useAmbientMotion } from '@/lib/motion';
import { STAGE_LABELS } from '@/lib/research/stages';
import { cn } from '@/lib/utils';

/** Long enough to read a two-word label, short enough that the bar visibly moves. */
const STEP_MS = 2200;

/**
 * The stages a run moves through, walked one at a time.
 *
 * Illustration, not telemetry: it names the product's real stages (the same
 * vocabulary the run page's checklist uses) and claims no counts or timings,
 * because a sign-in page has no run to measure. The label that changes is
 * hidden from assistive technology - announcing a new word every two seconds
 * would make the page unusable with a screen reader - and a static sentence
 * listing every stage stands in for it.
 *
 * Under reduced motion it does not cycle; it shows the whole pipeline at rest.
 */
export function PipelineTicker({ className }: { className?: string }) {
  const ambient = useAmbientMotion();
  const [step, setStep] = useState(0);

  useEffect(() => {
    if (!ambient) return;
    const timer = window.setInterval(
      () => setStep((current) => (current + 1) % RESEARCH_STAGES.length),
      STEP_MS,
    );
    return () => window.clearInterval(timer);
  }, [ambient]);

  const active = ambient ? step : RESEARCH_STAGES.length - 1;
  const stage = RESEARCH_STAGES[active] ?? RESEARCH_STAGES[0];

  return (
    <div className={cn('auth-chip rounded-2xl p-4', className)}>
      <p className="sr-only">
        A research run moves through {RESEARCH_STAGES.length} stages:{' '}
        {RESEARCH_STAGES.map((name) => STAGE_LABELS[name]).join(', ')}.
      </p>

      <div className="flex items-center justify-between gap-4 text-xs" aria-hidden>
        <span className="flex items-center gap-2 font-medium text-foreground/85">
          <span className="relative flex size-2">
            <span
              data-motion="loop"
              className="absolute -inset-1 animate-breathe rounded-full bg-primary/40"
            />
            <span className="relative size-2 rounded-full bg-primary" />
          </span>
          {/* Keyed so each stage arrives as a new node and gets the entrance. */}
          <span key={stage} className="reveal">
            {STAGE_LABELS[stage]}
          </span>
        </span>
        <span className="font-mono text-muted-foreground">
          {String(active + 1).padStart(2, '0')} / {String(RESEARCH_STAGES.length).padStart(2, '0')}
        </span>
      </div>

      <ol
        className="mt-3 grid gap-1.5"
        style={{ gridTemplateColumns: `repeat(${RESEARCH_STAGES.length}, minmax(0, 1fr))` }}
        aria-hidden
      >
        {RESEARCH_STAGES.map((name, index) => (
          <li key={name} className="h-1 overflow-hidden rounded-full bg-foreground/10">
            <span
              className={cn(
                'auth-segment block h-full origin-left rounded-full',
                'transition-transform duration-[var(--duration-slower)] ease-[var(--ease-out-soft)]',
                index <= active ? 'scale-x-100' : 'scale-x-0',
                index === active && ambient && 'auth-segment-live',
              )}
            />
          </li>
        ))}
      </ol>
    </div>
  );
}
