import { DEMO_USER } from '@/mocks/store';
import { fail, json, simulateLatency } from '../../_lib/respond';

export const dynamic = 'force-dynamic';

/**
 * Mock registration. Creates nothing: it mirrors the *shape* of the live
 * endpoint's success and refusal so the form can be developed and tested
 * without a backend, exactly as the mock login does (ADR 0009).
 *
 * The password floor is restated here because the live API's floor is a
 * setting, and a mock that accepted a six-character password would let a form
 * pass in mock mode and fail against the real thing.
 */
const MIN_PASSWORD_LENGTH = 12;

export async function POST(request: Request) {
  await simulateLatency(220, 480);

  let body: { email?: unknown; password?: unknown; name?: unknown };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return fail(400, 'invalid_body', 'Expected a JSON body.');
  }

  const email = typeof body.email === 'string' ? body.email.trim().toLowerCase() : '';
  const password = typeof body.password === 'string' ? body.password : '';
  const name = typeof body.name === 'string' ? body.name.trim() : '';

  const details: Record<string, string[]> = {};
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) details.email = ['Enter a valid email address.'];
  if (password.length < MIN_PASSWORD_LENGTH) {
    details.password = [`Use at least ${MIN_PASSWORD_LENGTH} characters.`];
  }
  if (Object.keys(details).length > 0) {
    return fail(422, 'validation_failed', 'Check the highlighted fields.', details);
  }

  // 201, like the live endpoint: the account is created and signed in.
  return json({ user: { ...DEMO_USER, email, name: name || DEMO_USER.name } }, 201);
}
