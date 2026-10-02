import { afterEach, describe, expect, it, vi } from 'vitest';

/**
 * Where a link the API hands back actually goes.
 *
 * The Google and GitHub buttons link to a path the API names on itself. In
 * local development the web app and the API are different origins (:3000 and
 * :8000), and a bare path resolved against the web app was a 404 instead of a
 * sign-in. The module reads its settings once at import, so each case imports
 * it fresh under its own environment.
 */
async function configUnder(env: Record<string, string>) {
  vi.resetModules();
  for (const [key, value] of Object.entries(env)) vi.stubEnv(key, value);
  return import('./config');
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe('apiHref', () => {
  it("sends a split-origin deployment to the API's origin, not the web app's", async () => {
    const { apiHref } = await configUnder({
      NEXT_PUBLIC_API_MODE: 'live',
      NEXT_PUBLIC_API_BASE_URL: 'http://localhost:8000/api/v1',
    });

    expect(apiHref('/api/v1/auth/sso/supabase/google/start')).toBe(
      'http://localhost:8000/api/v1/auth/sso/supabase/google/start',
    );
  });

  it('leaves a same-origin path alone', async () => {
    const { apiHref } = await configUnder({ NEXT_PUBLIC_API_MODE: 'mock' });

    expect(apiHref('/api/mock/v1/auth/sso/auth0/google/start')).toBe(
      '/api/mock/v1/auth/sso/auth0/google/start',
    );
  });
});
