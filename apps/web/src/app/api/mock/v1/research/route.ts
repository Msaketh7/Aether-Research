import { RESEARCH_MODES, type CreateResearchRequest } from '@aether/shared-types';
import { createRun, listRuns } from '@/mocks/store';
import { fail, json, simulateLatency } from '../_lib/respond';

export const dynamic = 'force-dynamic';

const MAX_LIMIT = 50;

export async function GET(request: Request) {
  await simulateLatency();
  const params = new URL(request.url).searchParams;
  const limit = Math.min(MAX_LIMIT, Math.max(1, Number(params.get('limit') ?? '20') || 20));

  return json(
    listRuns({
      limit,
      cursor: params.get('cursor'),
      status: params.get('status'),
      mode: params.get('mode'),
      q: params.get('q'),
    }),
  );
}

/**
 * FR-2. Returns 202: the run is queued, not executed. The API never runs a
 * research workflow inside the request thread - that constraint is honoured
 * here so the client is built against the real semantics from the start.
 */
export async function POST(request: Request) {
  await simulateLatency(220, 500);

  let body: CreateResearchRequest;
  try {
    body = (await request.json()) as CreateResearchRequest;
  } catch {
    return fail(400, 'invalid_body', 'Expected a JSON body.');
  }

  const details: Record<string, string[]> = {};
  const question = typeof body.question === 'string' ? body.question.trim() : '';
  if (question.length < 15) {
    details.question = ['A research question must be at least 15 characters.'];
  }
  if (question.length > 2000) {
    details.question = ['A research question must be at most 2000 characters.'];
  }
  if (!RESEARCH_MODES.includes(body.mode)) {
    details.mode = [`Mode must be one of: ${RESEARCH_MODES.join(', ')}.`];
  }
  if (body.depth !== undefined && (body.depth < 1 || body.depth > 5)) {
    details.depth = ['Depth must be between 1 and 5.'];
  }
  if (body.date_range_start && body.date_range_end && body.date_range_start > body.date_range_end) {
    details.date_range_end = ['The end date must not be before the start date.'];
  }
  if (Object.keys(details).length > 0) {
    return fail(422, 'validation_failed', 'Check the highlighted fields.', details);
  }

  const run = createRun({ ...body, question });
  return json(
    {
      run_id: run.id,
      status: run.status,
      events_url: `/research/${run.id}/events`,
    },
    202,
  );
}
