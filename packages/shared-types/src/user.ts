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

export interface LoginResponse {
  user: User;
}
