import type { IsoDateTime, Uuid } from './common';
import type { LlmProvider } from './enums';

/** The authenticated principal. Never carries a password hash or a token. */
export interface User {
  id: Uuid;
  email: string;
  name: string;
  role: 'user' | 'admin';
  created_at: IsoDateTime;
  last_login_at: IsoDateTime | null;
}

/** User-controlled preferences stored in `users.settings`. */
export interface UserSettings {
  /** Preferred provider when the user is allowed to override routing. */
  preferred_provider: LlmProvider | null;
  /** Default mode pre-selected on the new-research form. */
  default_mode: 'quick' | 'deep';
  /** Default depth for deep runs, 1..5. */
  default_depth: number;
  /** Email the user when a long run finishes. */
  notify_on_completion: boolean;
}

/** One active login. Shown in /settings so a device can be signed out. */
export interface SessionInfo {
  id: Uuid;
  user_agent: string;
  ip: string;
  created_at: IsoDateTime;
  expires_at: IsoDateTime;
  /** True for the session making the request; the UI must not offer to revoke it silently. */
  current: boolean;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface RegisterRequest {
  email: string;
  password: string;
  /** Optional; the API derives one from the address when it is blank. */
  name?: string;
}

/** Registration returns the same body as sign-in: the account is signed in. */
export interface LoginResponse {
  user: User;
}

/** How many other devices `DELETE /auth/sessions` signed out. */
export interface RevokedSessions {
  revoked: number;
}

// ---------------------------------------------------------------------------
// Single sign-on
// ---------------------------------------------------------------------------

/**
 * An identity provider this deployment can broker sign-in through.
 *
 * `local` is the first-party issuer that password sign-in mints tokens from.
 * It is named alongside the external two deliberately: since SSO replaced
 * opaque sessions, every credential path ends in a JWT, and having one name
 * for each issuer is what lets the frontend, the API and the audit log all
 * describe a sign-in the same way.
 */
export type IdentityProviderName = 'local' | 'auth0' | 'supabase';

/** An upstream social connection a provider brokers. */
export type SsoConnection = 'google' | 'github';

/**
 * One button on the sign-in page.
 *
 * The provider is part of the option rather than a global setting because the
 * same connection can be brokered by either provider, and which one a
 * deployment uses for Google is independent of which it uses for GitHub.
 */
export interface SsoOption {
  provider: Exclude<IdentityProviderName, 'local'>;
  connection: SsoConnection;
  /** Server-supplied, so relabelling a connection needs no frontend deploy. */
  label: string;
  /** Where the browser navigates to begin the flow. Always same-origin. */
  start_url: string;
}

/**
 * What the sign-in page may offer.
 *
 * Served rather than compiled in: which providers are configured is a property
 * of the deployment's environment, and a button for an unconfigured provider
 * is a dead end a user cannot distinguish from a broken one.
 */
export interface SsoOptions {
  options: SsoOption[];
  /** False when a deployment has turned off password sign-in entirely. */
  password_enabled: boolean;
  registration_enabled: boolean;
}

/**
 * Why a sign-in attempt came back to the frontend without a session.
 *
 * A closed set, because the callback reports failure through a query parameter
 * and an open one would let an upstream redirect write arbitrary text onto
 * the sign-in page.
 */
export type SsoFailure =
  | 'access_denied'
  | 'invalid_state'
  | 'exchange_failed'
  | 'email_unverified'
  | 'account_conflict'
  | 'provider_unavailable'
  | 'registration_closed'
  | 'unknown';

/** One linked upstream identity, as `/settings` lists it. */
export interface LinkedIdentity {
  id: Uuid;
  provider: IdentityProviderName;
  connection: SsoConnection | null;
  /** The address the provider asserted, which need not equal the account's. */
  email: string | null;
  created_at: IsoDateTime;
  last_used_at: IsoDateTime | null;
}
