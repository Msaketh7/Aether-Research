import type { ApiErrorBody } from '@aether/shared-types';
import { apiUrl } from './config';

/**
 * The single HTTP entry point for the browser.
 *
 * Components never call `fetch` directly: every request goes through here so
 * that error shape, credentials, abort handling and JSON parsing are decided
 * once. The error envelope matches the API contract in
 * `@aether/shared-types`, and unexpected failures are normalised into the same
 * shape so callers only ever handle `ApiError`.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, string[]> | undefined;
  readonly traceId: string | undefined;

  constructor(
    status: number,
    code: string,
    message: string,
    details?: Record<string, string[]>,
    traceId?: string,
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.details = details;
    this.traceId = traceId;
  }

  /** True when retrying could plausibly succeed. */
  get isRetryable(): boolean {
    return this.status === 0 || this.status === 429 || this.status >= 500;
  }

  get isAuthError(): boolean {
    return this.status === 401 || this.status === 403;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
  query?: Record<string, string | number | boolean | undefined | null>;
}

function buildUrl(path: string, query: RequestOptions['query']): string {
  const url = apiUrl(path);
  if (!query) return url;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${url}?${qs}` : url;
}

async function toApiError(response: Response): Promise<ApiError> {
  let body: ApiErrorBody | undefined;
  try {
    body = (await response.json()) as ApiErrorBody;
  } catch {
    // A non-JSON error body (proxy timeout, HTML error page) is still an error;
    // it just carries no structured detail.
  }
  const error = body?.error;
  return new ApiError(
    response.status,
    error?.code ?? `http_${response.status}`,
    error?.message ?? response.statusText ?? 'Request failed',
    error?.details,
    error?.trace_id,
  );
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, query } = options;

  let response: Response;
  try {
    response = await fetch(buildUrl(path, query), {
      method,
      // Session cookies; no token is ever stored in JS-readable storage.
      credentials: 'include',
      headers:
        body === undefined
          ? { Accept: 'application/json' }
          : {
              Accept: 'application/json',
              'Content-Type': 'application/json',
            },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: signal ?? null,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    throw new ApiError(0, 'network_error', 'Could not reach the Aether API.');
  }

  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as T;

  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError(
      response.status,
      'invalid_response',
      'The API returned a malformed response.',
    );
  }
}
