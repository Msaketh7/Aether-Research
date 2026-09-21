'use client';

import Link from 'next/link';
import { RunList } from '@/components/research/run-list';
import { AskComposer } from '@/components/research/ask-composer';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useResearchList } from '@/lib/api/queries';

/**
 * Home: the question box and nothing competing with it.
 *
 * This is where the product opens, because the thing a person came to do is
 * ask something. The dashboard still exists and still owns the numbers and the
 * full history; this screen carries only what you need to start, plus enough
 * recent work to pick something up again.
 */

/** Starting points, so an empty product is not a blank page. */
const SUGGESTIONS = [
  {
    label: 'Competitive landscape',
    question:
      'Compare the major AI inference infrastructure companies. Analyze their products, technology, pricing, funding, financial performance, recent announcements, risks, competitive advantages, and market opportunities.',
  },
  {
    label: 'Technology assessment',
    question:
      'What are the current state-of-the-art approaches to retrieval-augmented generation, and what measurable trade-offs distinguish them?',
  },
  {
    label: 'Regulatory scan',
    question:
      'What obligations do recent AI regulations place on providers of general-purpose models, and how do they differ by jurisdiction?',
  },
] as const;

export default function HomePage() {
  const runs = useResearchList({ limit: 3 });

  return (
    <div className="mx-auto flex max-w-3xl flex-col">
      {/*
       * Pushed off the top edge rather than centred in the viewport: vertical
       * centring puts the box in a different place on every screen height, and
       * on a short laptop it collides with the suggestions underneath it.
       */}
      <section aria-labelledby="ask-heading" className="pt-6 sm:pt-12">
        <h1
          id="ask-heading"
          className="font-display text-balance text-center text-[2rem] font-semibold leading-[1.1] sm:text-[2.6rem]"
        >
          What would you like researched?
        </h1>
        <p className="mx-auto mt-2 max-w-xl text-balance text-center text-sm leading-relaxed text-muted-foreground">
          Ask a complex question. Aether decomposes it, researches the parts in parallel, extracts
          evidence with verbatim spans and writes a report where every claim carries a citation.
        </p>

        <AskComposer className="mt-6" suggestions={SUGGESTIONS} autoFocus />
      </section>

      <section aria-label="Recent research" className="mt-12">
        <div className="mb-3 flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold">Recent research</h2>
          <Button asChild variant="ghost" size="sm" className="text-muted-foreground">
            <Link href="/dashboard">View all</Link>
          </Button>
        </div>

        {runs.isPending ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 3 }).map((_, index) => (
              <Skeleton key={index} className="h-28" />
            ))}
          </div>
        ) : runs.isError || runs.data.items.length === 0 ? (
          // No error panel and no empty-state illustration: this section is a
          // convenience under the question box, and a red box or a large "no
          // research yet" card would make the first screen of a new account
          // look like something had gone wrong. The dashboard reports both
          // properly.
          <p className="rounded-xl border border-dashed border-border px-4 py-6 text-center text-sm text-muted-foreground">
            {runs.isError
              ? 'Recent research could not be loaded. The dashboard has the full history.'
              : 'Runs you start will appear here.'}
          </p>
        ) : (
          <RunList runs={runs.data.items} />
        )}
      </section>
    </div>
  );
}
