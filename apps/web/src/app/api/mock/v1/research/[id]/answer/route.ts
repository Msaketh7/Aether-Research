import { getAnswer, getEntry } from '@/mocks/store';
import { json, notFound, simulateLatency } from '../../../_lib/respond';

export const dynamic = 'force-dynamic';

/**
 * `{ answer: null }` until the run has written one - never a 404.
 *
 * Unlike the report, this is the body of a conversation the client is already
 * rendering, and "not yet" is the normal state of a run that started ten
 * seconds ago. A client polling a 404 could not tell that apart from a run that
 * does not exist.
 */
export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  await simulateLatency();
  const { id } = await context.params;
  const entry = getEntry(id);
  if (!entry) return notFound('run');

  return json(getAnswer(entry));
}
