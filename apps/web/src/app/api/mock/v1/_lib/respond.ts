import type { ApiErrorBody } from '@aether/shared-types';
import { NextResponse } from 'next/server';

/**
 * Response helpers for the mock API (ADR 0009).
 *
 * The error envelope matches the contract the FastAPI service will implement,
 * so the client's error handling is exercised for real in Phase 1 rather than
 * being written blind and discovered wrong in Phase 2.
 */

export function json<T>(body: T, status = 200): NextResponse {
  return NextResponse.json(body, {
    status,
    headers: {
      // Run state is time-derived; a cached response would show a stale run.
      'Cache-Control': 'no-store',
    },
  });
}

export function fail(
  status: number,
  code: string,
  message: string,
  details?: Record<string, string[]>,
): NextResponse {
  const body: ApiErrorBody = { error: { code, message, ...(details ? { details } : {}) } };
  return NextResponse.json(body, { status, headers: { 'Cache-Control': 'no-store' } });
}

export const notFound = (what: string) =>
  fail(404, `${what}_not_found`, `No ${what.replace(/_/g, ' ')} with that id.`);

/** Mock latency, so loading states are visible during development. */
export async function simulateLatency(minMs = 60, maxMs = 180): Promise<void> {
  const speed = Math.max(1, Number(process.env.NEXT_PUBLIC_MOCK_SPEED ?? '1') || 1);
  const delay = (minMs + Math.random() * (maxMs - minMs)) / speed;
  await new Promise((resolve) => setTimeout(resolve, delay));
}
