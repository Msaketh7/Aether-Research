import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetRefreshState } from '@/lib/auth/refresh';
import { createTestQueryClient } from '@/test/render';
import { useResearchEvents } from './use-research-events';

/**
 * A browser closes an EventSource for good when a reconnect is answered with
 * anything but a 200, and the usual one is a 401: the server ends a stream
 * after SSE_MAX_CONNECTION_SECONDS, and a run that outlasts the fifteen-minute
 * access token reconnects without one. These pin the renewal and reopening
 * that keep a long run's stream alive, and the bounds on it.
 */

class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];

  readonly url: string;
  readyState = FakeEventSource.CONNECTING;
  private listeners = new Map<string, Set<(event: Event) => void>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: Event) => void) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type)!.add(listener);
  }

  removeEventListener(type: string, listener: (event: Event) => void) {
    this.listeners.get(type)?.delete(listener);
  }

  close() {
    this.readyState = FakeEventSource.CLOSED;
  }

  /** The browser's behaviour on a non-200 reconnect: closed, then `error`. */
  failForGood() {
    this.readyState = FakeEventSource.CLOSED;
    this.listeners.get('error')?.forEach((listener) => listener(new Event('error')));
  }

  /** A dropped connection the browser will retry by itself. */
  drop() {
    this.readyState = FakeEventSource.CONNECTING;
    this.listeners.get('error')?.forEach((listener) => listener(new Event('error')));
  }

  succeed() {
    this.readyState = FakeEventSource.OPEN;
    this.listeners.get('open')?.forEach((listener) => listener(new Event('open')));
  }
}

function refreshAnswers(status: number) {
  const fn = vi.fn().mockResolvedValue({ ok: status < 300, status, json: async () => ({}) });
  vi.stubGlobal('fetch', fn);
  return fn;
}

function subscribe() {
  const client = createTestQueryClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(() => useResearchEvents('run-1'), { wrapper });
}

/** Let the renewal settle and the reopening delay elapse. */
async function settle() {
  await act(async () => {
    await vi.runAllTimersAsync();
  });
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  vi.useFakeTimers();
  resetRefreshState();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('useResearchEvents after the access token expires', () => {
  it('renews the session and reopens a stream the browser closed', async () => {
    const fetchMock = refreshAnswers(200);
    subscribe();
    const first = FakeEventSource.instances[0]!;

    act(() => first.failForGood());
    await settle();

    expect(String(fetchMock.mock.calls[0]?.[0])).toMatch(/\/auth\/refresh$/);
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.instances[1]!.url).toBe(first.url);
  });

  it('leaves a dropped connection to the browser, which retries it itself', async () => {
    const fetchMock = refreshAnswers(200);
    subscribe();

    act(() => FakeEventSource.instances[0]!.drop());
    await settle();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it('does not reopen once the API refuses to renew', async () => {
    refreshAnswers(401);
    subscribe();

    act(() => FakeEventSource.instances[0]!.failForGood());
    await settle();

    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it('stops after a bounded number of reopenings that never open', async () => {
    // A stream that keeps failing after a renewal - a deleted run, an API
    // answering 500 - is not fixed by more renewals.
    const fetchMock = refreshAnswers(200);
    subscribe();

    for (let round = 0; round < 6; round += 1) {
      const latest = FakeEventSource.instances.at(-1)!;
      act(() => latest.failForGood());
      await settle();
    }

    expect(FakeEventSource.instances).toHaveLength(4);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('starts counting again once a reopened stream opens', async () => {
    refreshAnswers(200);
    subscribe();

    for (let round = 0; round < 5; round += 1) {
      const latest = FakeEventSource.instances.at(-1)!;
      act(() => latest.failForGood());
      await settle();
      act(() => FakeEventSource.instances.at(-1)!.succeed());
    }

    expect(FakeEventSource.instances).toHaveLength(6);
  });

  it('does not reopen after it has been unmounted', async () => {
    refreshAnswers(200);
    const { unmount } = subscribe();

    act(() => FakeEventSource.instances[0]!.failForGood());
    unmount();
    await settle();

    expect(FakeEventSource.instances).toHaveLength(1);
  });
});
