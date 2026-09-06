import { DEMO_USER } from '@/mocks/store';
import { json } from '../../_lib/respond';

export const dynamic = 'force-dynamic';

/** The mock API has a single always-signed-in demo user. */
export async function GET() {
  return json(DEMO_USER);
}
