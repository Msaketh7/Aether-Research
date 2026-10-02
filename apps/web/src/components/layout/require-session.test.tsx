import { screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetRefreshState } from '@/lib/auth/refresh';
import { renderWithProviders } from '@/test/render';
import { RequireSession } from './require-session';

const replace = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ replace }) }));

/**
 * The guard's contract is narrow and the boundary is elsewhere: the API refuses
 * every request without a session. What this must get right is *which* failure
 * sends someone to the sign-in form - being bounced there by an outage is the
 * least informative possible response to one.
 */

function respond(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    statusText: '',
    json: async () => body,
  });
}

const USER = {
  id: 'u1',
  email: 'ada@example.com',
  name: 'Ada',
  role: 'user',
  created_at: '2026-01-01T00:00:00Z',
  last_login_at: null,
};

beforeEach(() => {
  replace.mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetRefreshState();
});

describe('RequireSession', () => {
  it('renders the page for a signed-in caller', async () => {
    vi.stubGlobal('fetch', respond(200, USER));

    renderWithProviders(
      <RequireSession>
        <p>the dashboard</p>
      </RequireSession>,
    );

    expect(await screen.findByText('the dashboard')).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it('sends a signed-out caller to the sign-in form and renders nothing meanwhile', async () => {
    // Both the session check and the renewal it triggers are refused: signed
    // out for real, not merely expired.
    vi.stubGlobal(
      'fetch',
      respond(401, { error: { code: 'unauthenticated', message: 'Sign in to continue.' } }),
    );
    window.history.replaceState(null, '', '/research/abc?tab=evidence');

    renderWithProviders(
      <RequireSession>
        <p>the dashboard</p>
      </RequireSession>,
    );

    // The page they were on travels with them, so signing in puts them back.
    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith(
        `/login?${new URLSearchParams({ next: '/research/abc?tab=evidence' }).toString()}`,
      ),
    );
    expect(screen.queryByText('the dashboard')).not.toBeInTheDocument();
    window.history.replaceState(null, '', '/');
  });

  it('keeps a caller whose access token expired, by renewing it', async () => {
    // The fifteen-minute access token is gone but the session is not: the
    // client renews it and the check succeeds on the retry, with no visit to
    // the sign-in form in between.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: false,
        status: 401,
        statusText: '',
        json: async () => ({ error: { code: 'unauthenticated', message: 'Sign in.' } }),
      })
      .mockResolvedValueOnce({ ok: true, status: 200, statusText: '', json: async () => ({}) })
      .mockResolvedValueOnce({ ok: true, status: 200, statusText: '', json: async () => USER });
    vi.stubGlobal('fetch', fetchMock);

    renderWithProviders(
      <RequireSession>
        <p>the dashboard</p>
      </RequireSession>,
    );

    expect(await screen.findByText('the dashboard')).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
    expect(String(fetchMock.mock.calls[1]?.[0])).toMatch(/\/auth\/refresh$/);
  });

  it('leaves the page mounted when the API is broken rather than signed out', async () => {
    vi.stubGlobal(
      'fetch',
      respond(503, {
        error: { code: 'dependency_unavailable', message: 'A required service is unavailable.' },
      }),
    );

    renderWithProviders(
      <RequireSession>
        <p>the dashboard</p>
      </RequireSession>,
    );

    // Each page renders its own error state, which says what actually happened.
    expect(await screen.findByText('the dashboard')).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });
});
