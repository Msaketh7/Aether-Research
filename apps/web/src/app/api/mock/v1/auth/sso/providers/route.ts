import type { SsoOptions } from '@aether/shared-types';
import { json, simulateLatency } from '../../../_lib/respond';

export const dynamic = 'force-dynamic';

/**
 * Which sign-in methods the demo build offers.
 *
 * Both providers and both connections, so the sign-in page renders its full
 * surface in the mock build and the screenshots show what the product
 * actually looks like. The live API serves this from configuration and omits
 * any provider whose credentials are absent, so a real deployment shows
 * fewer buttons rather than broken ones.
 */
export async function GET() {
  await simulateLatency(40, 120);

  const body: SsoOptions = {
    options: [
      {
        provider: 'auth0',
        connection: 'google',
        label: 'Google',
        start_url: '/api/mock/v1/auth/sso/auth0/google/start',
      },
      {
        provider: 'auth0',
        connection: 'github',
        label: 'GitHub',
        start_url: '/api/mock/v1/auth/sso/auth0/github/start',
      },
    ],
    password_enabled: true,
    registration_enabled: true,
  };

  return json(body);
}
