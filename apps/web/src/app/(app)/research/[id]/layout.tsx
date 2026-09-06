import type { ReactNode } from 'react';
import { RunProvider } from '@/components/research/run-context';
import { RunHeader } from '@/components/research/run-header';

export const metadata = { title: 'Research run' };

/**
 * Run shell. The SSE subscription and the run query live here so they survive
 * navigation between the run's tabs.
 */
export default async function RunLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <RunProvider runId={id}>
      <div className="mx-auto max-w-6xl">
        <RunHeader />
        {children}
      </div>
    </RunProvider>
  );
}
