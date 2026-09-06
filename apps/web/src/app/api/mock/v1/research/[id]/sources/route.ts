import { getEntry, getSources } from '@/mocks/store';
import { json, notFound, simulateLatency } from '../../../_lib/respond';

export const dynamic = 'force-dynamic';

export async function GET(request: Request, context: { params: Promise<{ id: string }> }) {
  await simulateLatency();
  const { id } = await context.params;
  const entry = getEntry(id);
  if (!entry) return notFound('run');
  const type = new URL(request.url).searchParams.get('type');
  return json(getSources(entry, type));
}
