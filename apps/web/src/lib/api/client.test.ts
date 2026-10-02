import { afterEach, describe, expect, it, vi } from 'vitest';
import { resetRefreshState } from '@/lib/auth/refresh';
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
  vi.useRealTimers();
  resetRefreshState();
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

/**
 * The access token lives fifteen minutes, so a 401 is usually an expiry rather
 * than a sign-out. Before ADR 0022's refresh endpoint had a caller, every
 * signed-in person was sent back to the sign-in page a quarter of an hour after
 * arriving; these pin the renewal that prevents that, and the cases where it
 * must not happen.
 */
describe('apiRequest after the access token expires', () => {
  type Reply = { status: number; body?: unknown };

  function replies(...sequence: Reply[]) {
    const fn = vi.fn();
    for (const { status, body = {} } of sequence) {
      fn.mockResolvedValueOnce({
        ok: status >= 200 && status < 300,
        status,
        statusText: '',
        json: async () => body,
      });
    }
    vi.stubGlobal('fetch', fn);
    return fn;
  }

  const UNAUTHENTICATED = {
    status: 401,
    body: { error: { code: 'unauthenticated', message: 'Sign in to continue.' } },
  };

  const urls = (fn: ReturnType<typeof vi.fn>) =>
    fn.mock.calls.map(
      ([url, init]) =>
        `${(init as RequestInit).method} ${new URL(String(url), 'http://x').pathname}`,
    );

  it('renews the session and retries the request once', async () => {
    const fetchMock = replies(
      UNAUTHENTICATED,
      { status: 200 },
      { status: 200, body: { id: 'run-1' } },
    );

    await expect(apiRequest('/research/run-1')).resolves.toEqual({ id: 'run-1' });
    expect(urls(fetchMock)).toEqual([
      expect.stringMatching(/^GET .*\/research\/run-1$/),
      expect.stringMatching(/^POST .*\/auth\/refresh$/),
      expect.stringMatching(/^GET .*\/research\/run-1$/),
    ]);
  });

  it('sends the same body again on the retry', async () => {
    const fetchMock = replies(UNAUTHENTICATED, { status: 200 }, { status: 202, body: { id: 'r' } });

    await apiRequest('/research', { method: 'POST', body: { question: 'Why?' } });

    const first = fetchMock.mock.calls[0]![1] as RequestInit;
    const retry = fetchMock.mock.calls[2]![1] as RequestInit;
    expect(retry.method).toBe('POST');
    expect(retry.body).toBe(first.body);
  });

  it('passes the 401 on when the API refuses to renew', async () => {
    const fetchMock = replies(UNAUTHENTICATED, UNAUTHENTICATED);

    const error = (await apiRequest('/auth/me').catch((caught: unknown) => caught)) as ApiError;

    expect(error.status).toBe(401);
    expect(error.isAuthError).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('retries only once, so a revoked session cannot loop', async () => {
    const fetchMock = replies(UNAUTHENTICATED, { status: 200 }, UNAUTHENTICATED);

    const error = (await apiRequest('/auth/me').catch((caught: unknown) => caught)) as ApiError;

    expect(error.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('reports an unreachable refresh as a network failure, not a sign-out', async () => {
    // A 401 here would send the person to the sign-in form over an outage.
    replies(UNAUTHENTICATED, { status: 503 });

    const error = (await apiRequest('/research').catch((caught: unknown) => caught)) as ApiError;

    expect(error.status).toBe(0);
    expect(error.code).toBe('network_error');
    expect(error.isRetryable).toBe(true);
  });

  it('does not renew on a wrong password', async () => {
    const fetchMock = replies({
      status: 401,
      body: { error: { code: 'invalid_credentials', message: 'Check your email and password.' } },
    });

    const error = (await apiRequest('/auth/login', {
      method: 'POST',
      body: { email: 'a@b.co', password: 'nope' },
    }).catch((caught: unknown) => caught)) as ApiError;

    expect(error.code).toBe('invalid_credentials');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('does not renew on a 403, which is a refusal rather than an expiry', async () => {
    const fetchMock = replies({
      status: 403,
      body: { error: { code: 'forbidden', message: 'No.' } },
    });

    await apiRequest('/research/someone-elses').catch(() => undefined);

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('renews once for requests that expire together', async () => {
    // Three queries on one page all 401 at the same moment. One renewal, or
    // the second presentation of the refresh token revokes the session.
    // A renewal takes time, so the clock moves while it is in flight - which is
    // what lets a 401 arriving after it see that it is already answered.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(1_000);
    let renewed = false;
    const fetchMock = vi.fn().mockImplementation(async (url: string) => {
      if (String(url).endsWith('/auth/refresh')) {
        vi.setSystemTime(2_000);
        renewed = true;
        return { ok: true, status: 200, json: async () => ({}) };
      }
      return renewed
        ? { ok: true, status: 200, json: async () => ({ url }) }
        : { ok: false, status: 401, statusText: '', json: async () => UNAUTHENTICATED.body };
    });
    vi.stubGlobal('fetch', fetchMock);

    await expect(
      Promise.all([apiRequest('/research/a'), apiRequest('/research/b'), apiRequest('/stats')]),
    ).resolves.toHaveLength(3);

    const renewals = fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/auth/refresh'));
    expect(renewals).toHaveLength(1);
  });
});
