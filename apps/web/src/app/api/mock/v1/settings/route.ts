import type { UserSettings } from '@aether/shared-types';
import { getSettings, updateSettings } from '@/mocks/store';
import { fail, json } from '../_lib/respond';

export const dynamic = 'force-dynamic';

export async function GET() {
  return json(getSettings());
}

export async function PATCH(request: Request) {
  let patch: Partial<UserSettings>;
  try {
    patch = (await request.json()) as Partial<UserSettings>;
  } catch {
    return fail(400, 'invalid_body', 'Expected a JSON body.');
  }
  if (patch.default_depth !== undefined && (patch.default_depth < 1 || patch.default_depth > 5)) {
    return fail(422, 'validation_failed', 'Depth must be between 1 and 5.', {
      default_depth: ['Depth must be between 1 and 5.'],
    });
  }
  return json(updateSettings(patch));
}
