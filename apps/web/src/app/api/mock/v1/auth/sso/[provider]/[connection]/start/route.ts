import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

/**
 * The mock build's stand-in for an authorization request.
 *
 * There is no provider to redirect to and no session to mint, so this lands
 * the visitor where a real round trip would have. It exists so the button is
 * not a dead end during development; it is not a simulation of the protocol,
 * and nothing about the real flow can be exercised through it. The state,
 * PKCE and nonce checks that make the live flow safe are the API's, and they
 * are tested there against a scripted provider.
 */
export async function GET(request: Request) {
  const next = new URL(request.url).searchParams.get('next');

  // Same rule as the frontend's `safeDestination`, restated rather than
  // imported: this is a redirect target going into a `Location` header, and
  // the one thing it must never be is somebody else's origin.
  const safe =
    next && next.startsWith('/') && !next.startsWith('//') && !next.includes('\\') ? next : '/';

  return NextResponse.redirect(new URL(safe, request.url));
}
