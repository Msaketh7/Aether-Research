import { screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
    vi.stubGlobal(
      'fetch',
      respond(401, { error: { code: 'unauthenticated', message: 'Sign in to continue.' } }),
    );

    renderWithProviders(
      <RequireSession>
        <p>the dashboard</p>
      </RequireSession>,
    );

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/login'));
    expect(screen.queryByText('the dashboard')).not.toBeInTheDocument();
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
