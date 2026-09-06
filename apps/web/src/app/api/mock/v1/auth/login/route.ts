import { DEMO_USER } from '@/mocks/store';
import { fail, json, simulateLatency } from '../../_lib/respond';

export const dynamic = 'force-dynamic';

/**
 * Mock sign-in. Accepts any well-formed credentials: authentication is
 * implemented for real in Phase 2 (Auth.js + Postgres, FR-1). The shape of the
 * success and failure responses matches what that implementation will return.
 */
export async function POST(request: Request) {
  await simulateLatency(180, 420);

  let body: { email?: unknown; password?: unknown };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return fail(400, 'invalid_body', 'Expected a JSON body.');
  }

  const email = typeof body.email === 'string' ? body.email.trim() : '';
  const password = typeof body.password === 'string' ? body.password : '';

  const details: Record<string, string[]> = {};
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) details.email = ['Enter a valid email address.'];
  if (password.length < 8) details.password = ['Password must be at least 8 characters.'];
  if (Object.keys(details).length > 0) {
    return fail(422, 'validation_failed', 'Check the highlighted fields.', details);
  }

  return json({ user: { ...DEMO_USER, email } });
}
