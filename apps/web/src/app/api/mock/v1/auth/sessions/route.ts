import type { SessionInfo } from '@aether/shared-types';
import { stableUuid } from '@/mocks/rng';
import { json } from '../../_lib/respond';

export const dynamic = 'force-dynamic';

const DAY_MS = 86_400_000;

/** FR-1: the user can see and revoke their own sessions from /settings. */
export async function GET() {
  const now = Date.now();
  const sessions: SessionInfo[] = [
    {
      id: stableUuid('session:current'),
      user_agent: 'Chrome on Windows',
      ip: '203.0.113.24',
      created_at: new Date(now - 2 * 3_600_000).toISOString(),
      expires_at: new Date(now + 12 * DAY_MS).toISOString(),
      current: true,
    },
    {
      id: stableUuid('session:laptop'),
      user_agent: 'Safari on macOS',
      ip: '198.51.100.7',
      created_at: new Date(now - 5 * DAY_MS).toISOString(),
      expires_at: new Date(now + 9 * DAY_MS).toISOString(),
      current: false,
    },
  ];
  return json(sessions);
}

/** Sign out every device but this one. Mirrors `DELETE /auth/sessions`. */
export async function DELETE() {
  return json({ revoked: 1 });
}
