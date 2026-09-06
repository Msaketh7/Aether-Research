import { createRun, getEntry } from '@/mocks/store';
import { fail, json, notFound, simulateLatency } from '../../../_lib/respond';

export const dynamic = 'force-dynamic';

/**
 * Conversational research (PRD 5.3): a child run that reuses the parent's
 * evidence base instead of starting from zero.
 */
export async function POST(request: Request, context: { params: Promise<{ id: string }> }) {
  await simulateLatency(200, 420);
  const { id } = await context.params;
  const parent = getEntry(id);
  if (!parent) return notFound('run');

  let body: { question?: unknown };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return fail(400, 'invalid_body', 'Expected a JSON body.');
  }

  const question = typeof body.question === 'string' ? body.question.trim() : '';
  if (question.length < 10) {
    return fail(422, 'validation_failed', 'Check the highlighted fields.', {
      question: ['A follow-up question must be at least 10 characters.'],
    });
  }

  const run = createRun({
    question,
    mode: 'conversational',
    depth: parent.dataset.spec.depth,
    parent_run_id: id,
  });
  return json(
    { run_id: run.id, status: run.status, events_url: `/research/${run.id}/events` },
    202,
  );
}
