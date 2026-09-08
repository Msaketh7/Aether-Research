import { afterEach, describe, expect, it, vi } from 'vitest';

/**
 * The mock API must not be reachable from a live deployment.
 *
 * A pre-release audit finding: the route handlers under `/api/mock/v1` compile
 * into every build, so a deployment with `NEXT_PUBLIC_API_MODE=live` served an
 * unauthenticated sign-in endpoint and a corpus of fabricated research
 * alongside the real app. Nothing real leaks through them, but
 * research-shaped fiction on a public URL is not a surface to leave open.
 *
 * `vi.resetModules` before each import because the middleware reads the mode at
 * module scope, the way Next.js inlines `NEXT_PUBLIC_*` at build time.
 */

const ORIGINAL_MODE = process.env.NEXT_PUBLIC_API_MODE;

afterEach(() => {
  process.env.NEXT_PUBLIC_API_MODE = ORIGINAL_MODE;
  vi.resetModules();
});

async function runMiddleware(mode: string | undefined, path = '/api/mock/v1/auth/login') {
  if (mode === undefined) delete process.env.NEXT_PUBLIC_API_MODE;
  else process.env.NEXT_PUBLIC_API_MODE = mode;

  vi.resetModules();
  const { middleware, config } = await import('./middleware');
  const { NextRequest } = await import('next/server');

  const response = middleware(new NextRequest(new URL(`http://localhost:3000${path}`)));
  return { response, config };
}

describe('mock API gate', () => {
  it('returns 404 for mock routes in live mode', async () => {
    const { response } = await runMiddleware('live');

    expect(response.status).toBe(404);
  });

  it('lets mock routes through in mock mode', async () => {
    const { response } = await runMiddleware('mock');

    // `NextResponse.next()` is a pass-through, not a 404.
    expect(response.status).not.toBe(404);
  });

  it('lets mock routes through when the mode is unset', async () => {
    // Mock is the documented default; an unset variable must not break local
    // development, which is the only place the mock API is meant to run.
    const { response } = await runMiddleware(undefined);

    expect(response.status).not.toBe(404);
  });

  it('only matches the mock API, so real routes are untouched', async () => {
    const { config } = await runMiddleware('live');

    expect(config.matcher).toBe('/api/mock/:path*');
  });

  it('lets a real route through even in live mode', async () => {
    // The path is re-checked inside the middleware rather than trusted to the
    // matcher, so widening the matcher must not start 404-ing the real app.
    const { response } = await runMiddleware('live', '/dashboard');

    expect(response.status).not.toBe(404);
  });
});
