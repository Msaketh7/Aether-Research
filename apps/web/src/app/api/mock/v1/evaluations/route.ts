import { getEvaluations } from '@/mocks/store';
import { json, simulateLatency } from '../_lib/respond';

export const dynamic = 'force-dynamic';

export async function GET() {
  await simulateLatency();
  return json(getEvaluations());
}
