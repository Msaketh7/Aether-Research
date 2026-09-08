import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

/**
 * Closes the mock API outside mock mode.
 *
 * The route handlers under `/api/mock/v1` are compiled into every build,
 * including one deployed with `NEXT_PUBLIC_API_MODE=live`. Without this they
 * stay publicly reachable in production: a sign-in endpoint that accepts any
 * address with an eight-character password, and a corpus of fabricated
 * research that an integration could mistake for real results.
 *
 * They hold no real data and set no session cookie, so this is hardening
 * rather than a breach - but "unauthenticated endpoints that return
 * research-shaped fiction" is not a surface to leave open, and the project's
 * own rule is that mock data disappears once a real implementation exists
 * (ADR 0009). Until live mode is the only mode and the directory is deleted,
 * the gate is here.
 *
 * A 404 rather than a 403: in a live deployment those paths genuinely do not
 * exist, and saying so reveals less than refusing does.
 *
 * The path is re-checked here rather than trusted to `config.matcher` alone.
 * The matcher is one edit away from being widened for an unrelated reason, and
 * a security gate should not depend on a routing config staying narrow.
 */
export const MOCK_API_PREFIX = '/api/mock';

export function middleware(request: NextRequest) {
  const isMockRoute = request.nextUrl.pathname.startsWith(MOCK_API_PREFIX);

  if (isMockRoute && process.env.NEXT_PUBLIC_API_MODE === 'live') {
    return new NextResponse(null, { status: 404 });
  }
  return NextResponse.next();
}

export const config = {
  matcher: '/api/mock/:path*',
};
