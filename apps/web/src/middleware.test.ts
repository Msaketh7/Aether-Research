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

function jwt(claims: Record<string, unknown>): string {
  // Structurally a JWT, cryptographically meaningless. That is the point: the
  // middleware must not be verifying signatures, so a test that had to sign
  // one would be testing the wrong thing. Any token it accepts here is one the
  // API would reject - which is exactly the boundary this asserts.
  const encode = (value: object) => Buffer.from(JSON.stringify(value)).toString('base64url');
  return `${encode({ alg: 'RS256', typ: 'JWT' })}.${encode(claims)}.not-a-signature`;
}

const HOUR = 3600;
const future = () => Math.floor(Date.now() / 1000) + HOUR;
const past = () => Math.floor(Date.now() / 1000) - HOUR;

async function runMiddleware(
  mode: string | undefined,
  path = '/api/mock/v1/auth/login',
  cookie?: string,
) {
  if (mode === undefined) delete process.env.NEXT_PUBLIC_API_MODE;
  else process.env.NEXT_PUBLIC_API_MODE = mode;

  vi.resetModules();
  const { middleware, config } = await import('./middleware');
  const { NextRequest } = await import('next/server');

  const request = new NextRequest(new URL(`http://localhost:3000${path}`));
  if (cookie !== undefined) request.cookies.set('aether_session_at', cookie);

  const response = middleware(request);
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

  it('still matches the mock API now that the matcher covers the whole app', async () => {
    const { config } = await runMiddleware('live');

    // The matcher was narrowed to the mock prefix when the gate was the only
    // job. Route protection needs every page, so it is now an exclusion list -
    // and the mock prefix must still fall inside it.
    // Anchored, because Next matches a path against the whole pattern.
    // Unanchored, the negative lookahead simply succeeds further along the
    // string and every path appears to match.
    const pattern = new RegExp(`^${config.matcher[0]!}$`);
    expect(pattern.test('/api/mock/v1/auth/login')).toBe(true);
    expect(pattern.test('/_next/static/chunk.js')).toBe(false);
  });

  it('lets a real route through even in live mode', async () => {
    // The path is re-checked inside the middleware rather than trusted to the
    // matcher, so widening the matcher must not start 404-ing the real app.
    const { response } = await runMiddleware('live', '/dashboard');

    expect(response.status).not.toBe(404);
  });
});

describe('route protection', () => {
  it('sends a signed-out visitor on a guarded page to sign in', async () => {
    const { response } = await runMiddleware('live', '/dashboard');

    expect(response.status).toBe(307);
    expect(new URL(response.headers.get('location')!).pathname).toBe('/login');
  });

  it('carries the destination so the visitor lands where they were going', async () => {
    const { response } = await runMiddleware('live', '/research/abc?tab=evidence');

    const location = new URL(response.headers.get('location')!);
    expect(location.searchParams.get('next')).toBe('/research/abc?tab=evidence');
  });

  it('lets a visitor holding an unexpired token through', async () => {
    const { response } = await runMiddleware('live', '/dashboard', jwt({ exp: future() }));

    expect(response.status).not.toBe(307);
  });

  it('treats an expired token as no session', async () => {
    // Otherwise the visitor is routed in, every query 401s, and the app routes
    // them back out - a loop that reads as a broken session rather than an
    // expired one.
    const { response } = await runMiddleware('live', '/dashboard', jwt({ exp: past() }));

    expect(response.status).toBe(307);
  });

  it('treats a malformed cookie as no session rather than throwing', async () => {
    const { response } = await runMiddleware('live', '/dashboard', 'not.a.jwt');

    expect(response.status).toBe(307);
  });

  it('treats a token with no exp as no session', async () => {
    const { response } = await runMiddleware('live', '/dashboard', jwt({ sub: 'user-1' }));

    expect(response.status).toBe(307);
  });

  it('does not guard anything in mock mode', async () => {
    // The mock backend sets no cookie, so a guard here would bounce every
    // visitor between /dashboard and /login. This is what keeps the demo
    // build and the Playwright suite working.
    const { response } = await runMiddleware('mock', '/dashboard');

    expect(response.status).not.toBe(307);
  });

  it('does not guard a public page', async () => {
    const { response } = await runMiddleware('live', '/login');

    expect(response.status).not.toBe(307);
  });

  it('matches on path segments, so a similarly-named public route is untouched', async () => {
    // `/researchers` must not be caught by the `/research` prefix.
    const { response } = await runMiddleware('live', '/researchers');

    expect(response.status).not.toBe(307);
  });

  it('sends a signed-in visitor away from the sign-in page', async () => {
    const { response } = await runMiddleware('live', '/login', jwt({ exp: future() }));

    expect(response.status).toBe(307);
    expect(new URL(response.headers.get('location')!).pathname).toBe('/');
  });

  it('guards the home screen, which is where a signed-in visitor lands', async () => {
    // `/` is matched exactly rather than as a prefix. Listed as a prefix it
    // would match `/login` too and bounce a signed-out visitor forever.
    const { response } = await runMiddleware('live', '/');

    expect(response.status).toBe(307);
    expect(new URL(response.headers.get('location')!).pathname).toBe('/login');
  });

  it('does not accept a token in the redirect target', async () => {
    // A `next` that is an absolute URL would make the sign-in page a redirector
    // to anywhere. The value written here is always path-and-query from the
    // parsed URL, never anything reflected from a header.
    const { response } = await runMiddleware('live', '/dashboard');

    const next = new URL(response.headers.get('location')!).searchParams.get('next')!;
    expect(next.startsWith('/')).toBe(true);
    expect(next.startsWith('//')).toBe(false);
  });
});
