import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '@/test/render';
import RegisterPage from './page';

const push = vi.fn();
// `useSearchParams` as well as `useRouter`: the page renders the SSO
// failure banner, which reads the `?error=` the OAuth callback redirects
// back with. An empty set is the ordinary case - somebody arriving at the
// page directly rather than bouncing off a failed provider sign-in.
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => new URLSearchParams(),
}));

/**
 * The password policy lives on the server (Phase 20), so what this page owes
 * the user is that the server's refusal arrives next to the input that caused
 * it - not a second copy of the rules, which would be two policies to keep in
 * step and one of them would be wrong.
 */

function respond(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status < 400,
    status,
    statusText: '',
    json: async () => body,
  });
}

beforeEach(() => {
  push.mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('RegisterPage', () => {
  it('signs the new account in and goes to the question box', async () => {
    const fetchMock = respond(201, {
      user: {
        id: 'u1',
        email: 'ada@example.com',
        name: 'Ada',
        role: 'user',
        created_at: '2026-01-01T00:00:00Z',
        last_login_at: null,
      },
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<RegisterPage />);
    await user.type(screen.getByLabelText('Email'), 'ada@example.com');
    await user.type(screen.getByLabelText('Password'), 'correct-horse-battery-staple');
    await user.click(screen.getByTestId('register-submit'));

    await waitFor(() => expect(push).toHaveBeenCalledWith('/'));

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    // The session arrives as a cookie the browser must be allowed to store.
    expect(init.credentials).toBe('include');
  });

  it("renders the server's password policy beside the password field", async () => {
    vi.stubGlobal(
      'fetch',
      respond(422, {
        error: {
          code: 'validation_failed',
          message: 'That password cannot be used.',
          details: { password: ['Use at least 12 characters.'] },
        },
      }),
    );
    const user = userEvent.setup();

    renderWithProviders(<RegisterPage />);
    await user.type(screen.getByLabelText('Email'), 'ada@example.com');
    await user.type(screen.getByLabelText('Password'), 'short');
    await user.click(screen.getByTestId('register-submit'));

    expect(await screen.findByText('Use at least 12 characters.')).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });

  it('shows a refusal that names no field as a message rather than losing it', async () => {
    vi.stubGlobal(
      'fetch',
      respond(403, {
        error: {
          code: 'registration_closed',
          message: 'This deployment does not accept new registrations.',
        },
      }),
    );
    const user = userEvent.setup();

    renderWithProviders(<RegisterPage />);
    await user.type(screen.getByLabelText('Email'), 'ada@example.com');
    await user.type(screen.getByLabelText('Password'), 'correct-horse-battery-staple');
    await user.click(screen.getByTestId('register-submit'));

    expect(
      await screen.findByText('This deployment does not accept new registrations.'),
    ).toBeInTheDocument();
  });
});
