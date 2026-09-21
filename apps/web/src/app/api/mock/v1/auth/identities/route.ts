import type { LinkedIdentity } from '@aether/shared-types';
import { json, simulateLatency } from '../../_lib/respond';

export const dynamic = 'force-dynamic';

/**
 * The demo account's linked providers.
 *
 * One, so the settings page renders the populated state rather than the empty
 * one - the empty state is reachable by unlinking it, and the populated state
 * is what the screenshots should show. The live API serves real rows.
 */
export async function GET() {
  await simulateLatency(60, 160);

  const identities: LinkedIdentity[] = [
    {
      id: '11111111-1111-4111-8111-111111111111',
      provider: 'auth0',
      connection: 'google',
      email: 'analyst@aether.dev',
      created_at: '2026-09-01T09:14:00.000Z',
      last_used_at: '2026-09-18T16:02:00.000Z',
    },
  ];

  return json(identities);
}
