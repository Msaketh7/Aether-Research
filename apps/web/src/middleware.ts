import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';
import { ACCESS_COOKIE_NAME } from '@/lib/auth/cookies';

/**
 * Two jobs, and only one of them is a security control.
 *
 * **Closing the mock API outside mock mode — this one is a control.** The
 * route handlers under `/api/mock/v1` are compiled into every build, including
 * one deployed with `NEXT_PUBLIC_API_MODE=live`. Without this they stay
 * publicly reachable in production: a sign-in endpoint that accepts any
 * address with an eight-character password, and a corpus of fabricated
 * research that an integration could mistake for real results. A 404 rather
 * than a 403: in a live deployment those paths genuinely do not exist, and
 * saying so reveals less than refusing does.
 *
 * **Routing signed-out visitors to the sign-in page — this one is not.** It is
 * navigation, and it is written on the assumption that it can be defeated.
 * Three independent reasons it must never be the thing that protects data:
 *
 * 1. It reads the access token but does **not** verify its signature. Verifying
 *    means fetching the provider's JWKS, which is a network round trip this
 *    would then be doing on every navigation, against a dependency whose
 *    failure would take out routing. The API verifies properly, on every
 *    request, and that is the boundary.
 * 2. It cannot always see the cookie. The cookie belongs to the API's host, so
 *    this only reads it when the two share a registrable domain and the API
 *    sets `SESSION_COOKIE_DOMAIN` accordingly — true in production and true
 *    for `localhost` (cookies ignore the port), but not a thing to rely on.
 * 3. Middleware has been bypassable before. CVE-2025-29927 let a crafted
 *    `x-middleware-subrequest` header skip it entirely; Next 16 is patched,
 *    but a layer with that history does not get to be the only layer.
 *
 * It is also active only in live mode, for the reason given at the check.
 *
 * So a forged or expired cookie gets somebody the application shell and
 * nothing else: every page renders from data the API refuses to hand over
 * without a valid token. What this buys is that a signed-out person sees the
 * sign-in page instead of a dashboard full of loading skeletons that resolve
 * into errors.
 */
export const MOCK_API_PREFIX = '/api/mock';

/** Prefixes that require a session. Everything not listed is public. */
const GUARDED_PREFIXES = ['/dashboard', '/research', '/evaluations', '/settings'] as const;

/**
 * The home screen, guarded exactly rather than as a prefix: every path in the
 * product begins with `/`, so listing it above would make `isUnder` match the
 * sign-in page as well and bounce a signed-out visitor forever.
 */
const GUARDED_EXACT = ['/'] as const;

/** Pages that exist to get a session, and are pointless once you have one. */
const AUTH_PAGES = ['/login', '/register'] as const;

const DEFAULT_DESTINATION = '/';

export function middleware(request: NextRequest) {
  const { pathname, search } = request.nextUrl;

  // Re-checked here rather than trusted to `config.matcher` alone. The matcher
  // is one edit away from being widened for an unrelated reason, and a
  // security gate should not depend on a routing config staying narrow.
  if (pathname.startsWith(MOCK_API_PREFIX) && process.env.NEXT_PUBLIC_API_MODE === 'live') {
    return new NextResponse(null, { status: 404 });
  }

  // Route protection applies only when the app is pointed at a real API.
  // The mock backend authenticates nobody and sets no cookie - there is no
  // session for this to read - so guarding in mock mode would bounce every
  // visitor between `/dashboard` and `/login` forever. It costs nothing to
  // skip: mock mode holds no real data to protect, and the gate above is what
  // keeps it out of a live deployment in the first place.
  if (process.env.NEXT_PUBLIC_API_MODE !== 'live') return NextResponse.next();

  const signedIn = hasUnexpiredToken(request.cookies.get(ACCESS_COOKIE_NAME)?.value);

  const guarded =
    GUARDED_EXACT.some((page) => pathname === page) ||
    GUARDED_PREFIXES.some((prefix) => isUnder(pathname, prefix));

  if (!signedIn && guarded) {
    const url = request.nextUrl.clone();
    url.pathname = '/login';
    url.search = '';
    // Path and query only, taken from the parsed URL rather than from any
    // header. An absolute URL here would be reflected into the sign-in page's
    // redirect and is how this becomes an open redirect.
    url.searchParams.set('next', `${pathname}${search}`);
    return NextResponse.redirect(url);
  }

  if (signedIn && AUTH_PAGES.some((page) => isUnder(pathname, page))) {
    const url = request.nextUrl.clone();
    url.pathname = DEFAULT_DESTINATION;
    url.search = '';
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

/** `/research` matches `/research` and `/research/1`, but never `/researchers`. */
function isUnder(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

/**
 * Whether a token is present and not already past its own `exp`.
 *
 * The claim is read from an **unverified** payload, which is safe only because
 * of what it is used for: deciding whether to render the app shell. Trusting
 * it the other way would be the bug — a forged token with a distant `exp`
 * passes this, and is then refused by the API on the first request it makes.
 *
 * Checking `exp` at all is about not bouncing people. Without it, a browser
 * holding a day-old cookie is routed into the app, every query 401s, and the
 * app sends it back here — a redirect loop that looks like a broken session.
 */
function hasUnexpiredToken(token: string | undefined): boolean {
  if (!token) return false;

  const payload = decodeJwtPayload(token);
  if (payload === null) return false;

  const exp = payload.exp;
  if (typeof exp !== 'number') return false;

  // No leeway. Clock skew matters when *rejecting* a valid token; here the
  // cost of being a few seconds optimistic is one refused request, and the
  // refresh flow handles that.
  return exp * 1000 > Date.now();
}

function decodeJwtPayload(token: string): Record<string, unknown> | null {
  const parts = token.split('.');
  if (parts.length !== 3) return null;

  const segment = parts[1];
  if (segment === undefined) return null;

  try {
    // base64url -> base64. `atob` is what the edge runtime provides; Buffer
    // is not available there.
    const base64 = segment.replaceAll('-', '+').replaceAll('_', '/');
    const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=');
    const parsed: unknown = JSON.parse(atob(padded));
    return typeof parsed === 'object' && parsed !== null
      ? (parsed as Record<string, unknown>)
      : null;
  } catch {
    // Anything unparseable is treated as no session at all, which routes to
    // sign-in. A malformed cookie is indistinguishable from none, and both
    // have the same correct response.
    return null;
  }
}

export const config = {
  /**
   * Everything except Next's own assets and the files that must stay
   * reachable to a signed-out browser. The negative lookahead is the
   * documented way to do this: listing guarded paths here instead would mean
   * the public ones silently stop being matched when one is added above.
   */
  matcher: ['/((?!_next/static|_next/image|favicon.ico|robots.txt|sitemap.xml|.*\.png$).*)'],
};
