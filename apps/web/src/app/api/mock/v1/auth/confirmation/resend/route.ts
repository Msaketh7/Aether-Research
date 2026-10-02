import { fail, json, simulateLatency } from '../../../_lib/respond';

export const dynamic = 'force-dynamic';

/**
 * Mock "send the confirmation link again". Sends nothing: it mirrors the live
 * endpoint's shape - `202` and the same body for any well-formed address, so
 * the button can be developed without a mailer (ADR 0009, ADR 0025).
 */
export async function POST(request: Request) {
  await simulateLatency(180, 380);

  let body: { email?: unknown };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return fail(400, 'invalid_body', 'Expected a JSON body.');
  }

  const email = typeof body.email === 'string' ? body.email.trim().toLowerCase() : '';
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
    return fail(422, 'validation_failed', 'Check the highlighted fields.', {
      email: ['Enter a valid email address.'],
    });
  }

  return json({ confirmation_required: true, email }, 202);
}
