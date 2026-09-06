/**
 * Transport configuration (ADR 0009).
 *
 * The only difference between the Phase 1 prototype and the finished product is
 * the base URL. Everything above this file - hooks, components, error handling,
 * SSE reconnection - is identical in both modes.
 *
 * `process.env.NEXT_PUBLIC_*` is inlined at build time and must be referenced
 * with the full literal name; do not index it dynamically.
 */

export type ApiMode = 'mock' | 'live';

export const API_MODE: ApiMode = process.env.NEXT_PUBLIC_API_MODE === 'live' ? 'live' : 'mock';

/** Base URL for every REST and SSE request. Relative in mock mode. */
export const API_BASE_URL: string =
  API_MODE === 'live'
    ? (process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000/api/v1')
    : '/api/mock/v1';

export const APP_NAME: string = process.env.NEXT_PUBLIC_APP_NAME ?? 'Aether Research';

/**
 * Multiplier applied to the simulated research timeline in mock mode. 1 is
 * realistic pacing; the Playwright suite raises it so smoke tests stay fast.
 * Has no effect in live mode.
 */
export const MOCK_SPEED: number = Math.max(
  1,
  Number(process.env.NEXT_PUBLIC_MOCK_SPEED ?? '1') || 1,
);

export function apiUrl(path: string): string {
  return `${API_BASE_URL}${path.startsWith('/') ? path : `/${path}`}`;
}
