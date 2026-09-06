import { getEntry, getPlan } from '@/mocks/store';
import { json, notFound } from '../../../_lib/respond';

export const dynamic = 'force-dynamic';

export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  const entry = getEntry(id);
  if (!entry) return notFound('run');
  return json(getPlan(entry));
}
