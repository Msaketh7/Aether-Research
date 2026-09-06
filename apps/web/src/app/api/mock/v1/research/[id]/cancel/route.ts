import { cancelRun } from '@/mocks/store';
import { json, notFound, simulateLatency } from '../../../_lib/respond';

export const dynamic = 'force-dynamic';

/** Cooperative cancellation: the run stops at its next checkpoint. */
export async function POST(_request: Request, context: { params: Promise<{ id: string }> }) {
  await simulateLatency(120, 260);
  const { id } = await context.params;
  const run = cancelRun(id);
  if (!run) return notFound('run');
  return json(run);
}
