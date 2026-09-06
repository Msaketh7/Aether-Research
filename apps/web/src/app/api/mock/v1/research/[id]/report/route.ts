import { getEntry, getReport } from '@/mocks/store';
import { fail, json, notFound, simulateLatency } from '../../../_lib/respond';

export const dynamic = 'force-dynamic';

/**
 * The report exists only after synthesis and citation validation. A 404 here is
 * an expected state for a running run, not an error - the client treats it that
 * way rather than retrying.
 */
export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  await simulateLatency();
  const { id } = await context.params;
  const entry = getEntry(id);
  if (!entry) return notFound('run');

  const report = getReport(entry);
  if (!report) {
    return fail(404, 'report_not_ready', 'This run has not produced a validated report yet.');
  }
  return json(report);
}
