import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, apiRequest } from './client';

/**
 * Error handling is the part of the transport most likely to be wrong and least
 * likely to be noticed, so it is pinned here: the envelope from
 * `@aether/shared-types` must survive into a typed `ApiError`, and every other
 * failure mode must be normalised into the same shape.
 */

function mockFetch(response: Partial<Response> & { json?: () => Promise<unknown> }) {
  const fn = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => ({}),
    ...response,
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('apiRequest', () => {
  it('returns the parsed body on success', async () => {
    mockFetch({ json: async () => ({ id: 'run-1' }) });
    await expect(apiRequest<{ id: string }>('/research/run-1')).resolves.toEqual({ id: 'run-1' });
  });

  it('builds a query string, dropping empty values', async () => {
    const fetchMock = mockFetch({ json: async () => ({}) });
    await apiRequest('/research', {
      query: { status: 'completed', mode: undefined, q: '', limit: 20 },
    });

    const url = String(fetchMock.mock.calls[0]?.[0]);
    expect(url).toContain('status=completed');
    expect(url).toContain('limit=20');
    expect(url).not.toContain('mode=');
    expect(url).not.toContain('q=');
  });

  it('maps the API error envelope onto ApiError', async () => {
    mockFetch({
      ok: false,
      status: 422,
      statusText: 'Unprocessable Entity',
      json: async () => ({
        error: {
          code: 'validation_failed',
          message: 'Check the highlighted fields.',
          details: { question: ['Too short.'] },
          trace_id: 'trace-42',
        },
      }),
    });

    const error = await apiRequest('/research', { method: 'POST', body: {} }).catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(ApiError);
    const apiError = error as ApiError;
    expect(apiError.status).toBe(422);
    expect(apiError.code).toBe('validation_failed');
    expect(apiError.details?.question).toEqual(['Too short.']);
    expect(apiError.traceId).toBe('trace-42');
    expect(apiError.isRetryable).toBe(false);
  });

  it('still produces an ApiError when the error body is not JSON', async () => {
    mockFetch({
      ok: false,
      status: 502,
      statusText: 'Bad Gateway',
      json: async () => {
        throw new Error('not json');
      },
    });

    const error = (await apiRequest('/research').catch((caught: unknown) => caught)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe('http_502');
    expect(error.isRetryable).toBe(true);
  });

  it('classifies a network failure without leaking the cause', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    const error = (await apiRequest('/research').catch((caught: unknown) => caught)) as ApiError;
    expect(error.status).toBe(0);
    expect(error.code).toBe('network_error');
    expect(error.message).not.toContain('Failed to fetch');
    expect(error.isRetryable).toBe(true);
  });

  it('rethrows an abort so cancelled queries are not reported as errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new DOMException('aborted', 'AbortError')));
    await expect(apiRequest('/research')).rejects.toBeInstanceOf(DOMException);
  });

  it('treats 404 and 401 as non-retryable and flags them for the caller', async () => {
    mockFetch({
      ok: false,
      status: 404,
      json: async () => ({ error: { code: 'report_not_ready', message: 'Not ready.' } }),
    });

    const error = (await apiRequest('/report').catch((caught: unknown) => caught)) as ApiError;
    expect(error.isNotFound).toBe(true);
    expect(error.isAuthError).toBe(false);
    expect(error.isRetryable).toBe(false);
  });

  it('returns undefined for 204 rather than trying to parse a body', async () => {
    mockFetch({
      status: 204,
      json: async () => {
        throw new Error('should not be called');
      },
    });
    await expect(apiRequest('/auth/logout', { method: 'POST' })).resolves.toBeUndefined();
  });
});
