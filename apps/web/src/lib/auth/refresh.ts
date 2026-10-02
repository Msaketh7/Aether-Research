import { apiUrl } from '@/lib/api/config';

/**
 * Renewing the session without asking the person to sign in again (ADR 0022).
 *
 * The access token lives fifteen minutes and its cookie is deleted with it; the
 * refresh token lives fourteen days but is scoped to the refresh endpoint's own
 * path, so nothing else - this code included - can see whether one exists. The
 * only way to find out is to ask: POST to that path, and the browser attaches
 * the cookie if there is one. A renewal sets both cookies again on the
 * response, so there is nothing for script to store or read.
 *
 * Two rules make it safe to call from anywhere:
 *
 * **One renewal at a time, across every tab.** The refresh token is rotated on
 * every use, and presenting one that has already been used is treated as theft:
 * the API revokes the whole family and everybody signs in again. Two renewals
 * in flight at once - two queries that 401 together, or two tabs whose tokens
 * expired at the same moment - would both carry the same token, and the second
 * would sign the person out. So calls share one promise within a tab, and tabs
 * queue on a Web Lock, which makes the second tab's request leave only after
 * the first one's response has replaced the cookie.
 *
 * **A renewal that already happened is reused, not repeated.** The cookie jar
 * is shared, so once any renewal - this tab's or another's - finishes after a
 * request was sent, that request's 401 is already answered: retrying it will
 * carry the new token. Renewing again would rotate the token for nothing, so
 * the time of the last renewal is recorded and compared with when the failed
 * request left - strictly, because within one millisecond the order is
 * unknown, and the safe guess is a redundant renewal rather than a retry that
 * carries the old token and reads as a sign-out.
 */

/** What became of a renewal attempt. Callers treat each differently. */
export type RefreshOutcome =
  /** New tokens are in the cookie jar; retry what failed. */
  | 'renewed'
  /** The API said no - no refresh token, a revoked or reused one. Sign in. */
  | 'refused'
  /** The API could not be reached. Not a verdict about the session. */
  | 'unreachable';

const LOCK_NAME = 'aether:session-refresh';
const RENEWED_AT_KEY = 'aether:session-renewed-at';

let inFlight: Promise<RefreshOutcome> | null = null;

/** This tab's own record, for when storage is unavailable. */
let renewedHere = 0;

/**
 * Renew the session, or join the renewal already under way.
 *
 * `sentAt` is when the request that was refused left this tab. A renewal that
 * finished after it means the refusal is already answered.
 */
export function refreshSession(sentAt: number = Date.now()): Promise<RefreshOutcome> {
  if (lastRenewal() > sentAt) return Promise.resolve('renewed');
  inFlight ??= renewAcrossTabs(sentAt).finally(() => {
    inFlight = null;
  });
  return inFlight;
}

async function renewAcrossTabs(sentAt: number): Promise<RefreshOutcome> {
  const renew = async (): Promise<RefreshOutcome> => {
    // Checked again inside the lock: another tab may have renewed while this
    // one waited for it.
    if (lastRenewal() > sentAt) return 'renewed';
    const outcome = await requestRenewal();
    if (outcome === 'renewed') recordRenewal();
    return outcome;
  };

  // Every browser this app supports has Web Locks. Without them a tab still
  // shares one renewal between its own requests, which is the common case; two
  // tabs renewing in the same instant is the narrow one left open.
  const locks = typeof navigator === 'undefined' ? undefined : navigator.locks;
  return locks ? locks.request(LOCK_NAME, renew) : renew();
}

async function requestRenewal(): Promise<RefreshOutcome> {
  let response: Response;
  try {
    response = await fetch(apiUrl('/auth/refresh'), {
      method: 'POST',
      credentials: 'include',
      headers: { Accept: 'application/json' },
    });
  } catch {
    return 'unreachable';
  }
  if (response.ok) return 'renewed';
  // A 429 from the credential rate limit, or a 5xx, says nothing about whether
  // the session is still good; only an explicit refusal does.
  return response.status === 401 || response.status === 403 ? 'refused' : 'unreachable';
}

/**
 * When some tab last renewed, in milliseconds. Storage can be unavailable - a
 * private window, blocked site data - and then each tab knows only its own
 * renewals, which is still correct because the lock serialises the rest.
 */
function lastRenewal(): number {
  try {
    return Math.max(renewedHere, Number(window.localStorage.getItem(RENEWED_AT_KEY)) || 0);
  } catch {
    return renewedHere;
  }
}

function recordRenewal(): void {
  renewedHere = Date.now();
  try {
    window.localStorage.setItem(RENEWED_AT_KEY, String(renewedHere));
  } catch {
    // See lastRenewal: losing this costs a redundant renewal, not a session.
  }
}

/**
 * Paths where a 401 means something other than "the access token expired".
 *
 * A wrong password at sign-in is a 401, and renewing a session in response to
 * it would be meaningless at best; a 401 from the refresh endpoint itself is
 * the answer, and asking again would loop.
 */
const NO_RENEWAL_PATHS = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/logout'];

export function renewsOnUnauthorized(path: string): boolean {
  const bare = path.split('?', 1)[0] ?? path;
  return !NO_RENEWAL_PATHS.includes(bare);
}

/** Forget every renewal this tab knows of. For tests. */
export function resetRefreshState(): void {
  inFlight = null;
  renewedHere = 0;
  try {
    window.localStorage.removeItem(RENEWED_AT_KEY);
  } catch {
    // Nothing to forget.
  }
}
