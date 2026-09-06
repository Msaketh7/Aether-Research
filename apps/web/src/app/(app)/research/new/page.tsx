import { PageHeader } from '@/components/common/page-header';
import { NewResearchForm } from '@/components/research/new-research-form';

export const metadata = { title: 'New research' };

/**
 * Server component: reads the optional `?q=` seed from a dashboard template and
 * hands it to the client form.
 */
export default async function NewResearchPage({
  searchParams,
}: {
  searchParams: Promise<{ q?: string | string[] }>;
}) {
  const params = await searchParams;
  const raw = Array.isArray(params.q) ? params.q[0] : params.q;
  const initialQuestion = typeof raw === 'string' ? raw.slice(0, 2000) : '';

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title="New research"
        description="Ask a complex question. Aether decomposes it, researches the parts in parallel, extracts evidence, checks for contradictions and writes a cited report."
      />
      <NewResearchForm initialQuestion={initialQuestion} />
    </div>
  );
}
