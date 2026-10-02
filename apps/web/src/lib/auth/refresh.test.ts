import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { refreshSession, renewsOnUnauthorized, resetRefreshState } from './refresh';

/**
 * The refresh token is rotated on every use and a reused one revokes the whole
 * session, so the property that matters most here is not that a renewal works -
 * it is that two of them never leave at once carrying the same token. Each test
 * below is one of the ways that could happen.
 */

function respond(status: number) {
  return vi.fn().mockImplementation(async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => ({}),
  }));
}

/** A fetch whose responses are released by the test, one at a time. */
function heldFetch() {
  const release: Array<() => void> = [];
  const fn = vi.fn().mockImplementation(
    () =>
      new Promise((resolve) => {
        release.push(() => resolve({ ok: true, status: 200, json: async () => ({}) }));
      }),
  );
  return { fn, release };
}

beforeEach(() => {
  resetRefreshState();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('refreshSession', () => {
  it('posts to the refresh endpoint with the cookies and reports a renewal', async () => {
    const fetchMock = respond(200);
    vi.stubGlobal('fetch', fetchMock);

    await expect(refreshSession()).resolves.toBe('renewed');

    const [url, init] = fetchMock.mock.calls[0]!;
    expect(String(url)).toMatch(/\/auth\/refresh$/);
    expect(init).toMatchObject({ method: 'POST', credentials: 'include' });
  });

  it('shares one renewal between callers that ask at the same time', async () => {
    // Two queries that 401 together must not each present the refresh token:
    // the second presentation is a reuse, and a reuse signs the person out.
    const { fn, release } = heldFetch();
    vi.stubGlobal('fetch', fn);

    const first = refreshSession();
    const second = refreshSession();
    await vi.waitFor(() => expect(release).toHaveLength(1));
    release[0]!();

    await expect(Promise.all([first, second])).resolves.toEqual(['renewed', 'renewed']);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('answers a request sent before the last renewal without renewing again', async () => {
    // The request carried the old token, but the cookie jar now holds the new
    // one - retrying is enough, and a second renewal would rotate for nothing.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(1_000);
    const sentAt = Date.now();
    vi.setSystemTime(2_000);
    const fetchMock = respond(200);
    vi.stubGlobal('fetch', fetchMock);

    await refreshSession();
    await expect(refreshSession(sentAt)).resolves.toBe('renewed');

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('renews again for a request sent after the last renewal', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(1_000);
    const fetchMock = respond(200);
    vi.stubGlobal('fetch', fetchMock);

    await refreshSession();
    vi.setSystemTime(5_000);
    await refreshSession(Date.now());

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('treats a renewal finished in the same millisecond as not yet seen', async () => {
    // Within one millisecond the order is unknown. Guessing "already renewed"
    // would retry with the old token and read as a sign-out; guessing "not
    // yet" costs one redundant rotation.
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(1_000);
    const fetchMock = respond(200);
    vi.stubGlobal('fetch', fetchMock);

    await refreshSession();
    await refreshSession(1_000);

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('sees a renewal another tab recorded', async () => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(1_000);
    window.localStorage.setItem('aether:session-renewed-at', '5000');
    const fetchMock = respond(200);
    vi.stubGlobal('fetch', fetchMock);

    await expect(refreshSession(2_000)).resolves.toBe('renewed');
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('queues on a Web Lock so tabs renew one after another', async () => {
    const request = vi.fn((_name: string, callback: () => Promise<unknown>) => callback());
    vi.stubGlobal('navigator', { ...navigator, locks: { request } });
    vi.stubGlobal('fetch', respond(200));

    await refreshSession();

    expect(request).toHaveBeenCalledWith('aether:session-refresh', expect.any(Function));
  });

  it.each([401, 403])('reports a %i as a refusal', async (status) => {
    vi.stubGlobal('fetch', respond(status));
    await expect(refreshSession()).resolves.toBe('refused');
  });

  it.each([429, 500, 503])(
    'reports a %i as unreachable, because it says nothing about the session',
    async (status) => {
      vi.stubGlobal('fetch', respond(status));
      await expect(refreshSession()).resolves.toBe('unreachable');
    },
  );

  it('reports a network failure as unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await expect(refreshSession()).resolves.toBe('unreachable');
  });

  it('does not record a refused renewal as one that happened', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({}) })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({}) });
    vi.stubGlobal('fetch', fetchMock);

    await expect(refreshSession()).resolves.toBe('refused');
    await expect(refreshSession(0)).resolves.toBe('renewed');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

describe('renewsOnUnauthorized', () => {
  it.each(['/auth/login', '/auth/register', '/auth/refresh', '/auth/logout', '/auth/login?x=1'])(
    'leaves a 401 from %s alone, because it is the answer rather than an expiry',
    (path) => {
      expect(renewsOnUnauthorized(path)).toBe(false);
    },
  );

  it.each(['/auth/me', '/auth/sessions', '/research', '/research/abc/report'])(
    'renews on a 401 from %s',
    (path) => {
      expect(renewsOnUnauthorized(path)).toBe(true);
    },
  );
});
