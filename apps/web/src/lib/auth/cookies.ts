/**
 * The names of the cookies the API sets, mirrored for the middleware.
 *
 * The API owns these cookies entirely - it sets them, it clears them, and the
 * browser never exposes them to script because they are `HttpOnly`. The only
 * reason the frontend knows their names at all is that middleware runs on the
 * server and reads the access cookie to decide where to route a visitor.
 *
 * Kept in step with `session_cookie_name` in the API's settings by the
 * environment variable, which both processes read. A mismatch is not a
 * security problem - the API still validates what it receives - but it makes
 * every signed-in person look signed out to the router, so it is worth the
 * one variable rather than two hardcoded strings drifting apart.
 */

const DEFAULT_COOKIE_PREFIX = 'aether_session';

const PREFIX: string = process.env.NEXT_PUBLIC_SESSION_COOKIE_NAME ?? DEFAULT_COOKIE_PREFIX;

/** The access token. Short-lived, sent with every API request. */
export const ACCESS_COOKIE_NAME = `${PREFIX}_at`;

/**
 * The refresh token. Named here for completeness only: it is path-scoped to
 * the refresh endpoint, so it is not sent to the frontend's origin at all and
 * the middleware will never see it.
 */
export const REFRESH_COOKIE_NAME = `${PREFIX}_rt`;
