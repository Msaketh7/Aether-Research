import { DEMO_USER } from '@/mocks/store';
import { json } from '../../_lib/respond';

export const dynamic = 'force-dynamic';

/**
 * Mock renewal. The mock API has one always-signed-in demo user and sets no
 * cookies, so there is never anything to renew - but the route exists so the
 * mock answers the same contract as the live `POST /auth/refresh`: a renewed
 * session returns the user, as sign-in does.
 */
export async function POST() {
  return json({ user: DEMO_USER });
}
