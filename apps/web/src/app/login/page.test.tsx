import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '@/test/render';
import LoginPage from './page';

const push = vi.fn();
// Read lazily, at render: each test sets the query string it arrives with.
let search = new URLSearchParams();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push }),
  useSearchParams: () => search,
}));

function json(status: number, body: unknown) {
  return { ok: status < 400, status, statusText: '', json: async () => body };
}

/** The deployment's sign-in options, then whatever the login endpoint says. */
function api(login: ReturnType<typeof json>) {
  return vi.fn(async (url: string) =>
    url.includes('/sso/providers')
      ? json(200, { options: [], password_enabled: true, registration_enabled: true })
      : url.includes('/confirmation/resend')
        ? json(202, { confirmation_required: true, email: 'analyst@aether.dev' })
        : login,
  );
}

beforeEach(() => {
  push.mockClear();
  search = new URLSearchParams();
  window.history.replaceState(null, '', '/login');
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('LoginPage', () => {
  it('names the page in its one heading', () => {
    vi.stubGlobal('fetch', api(json(204, null)));
    renderWithProviders(<LoginPage />);

    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(screen.getByRole('heading', { level: 1, name: 'Welcome back' })).toBeInTheDocument();
  });

  it('shows a refusal and gives the button back, rather than leaving it spinning', async () => {
    vi.stubGlobal(
      'fetch',
      api(
        json(401, {
          error: {
            code: 'invalid_credentials',
            message: 'That email and password do not match an account.',
          },
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    const email = await screen.findByLabelText('Email');
    await user.clear(email);
    await user.type(email, 'nobody@example.com');
    await user.click(screen.getByTestId('login-submit'));

    expect(
      await screen.findByText('That email and password do not match an account.'),
    ).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId('login-submit')).toBeEnabled());
    expect(screen.getByTestId('login-submit')).toHaveTextContent('Sign in');
    expect(push).not.toHaveBeenCalled();
  });

  it('confirms success on the button and goes to the question box', async () => {
    vi.stubGlobal(
      'fetch',
      api(
        json(200, {
          user: {
            id: 'u1',
            email: 'ada@example.com',
            name: 'Ada',
            role: 'user',
            created_at: '2026-01-01T00:00:00Z',
            last_login_at: null,
          },
        }),
      ),
    );
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await user.click(await screen.findByTestId('login-submit'));

    await waitFor(() => expect(push).toHaveBeenCalledWith('/'));
    expect(screen.getByTestId('login-submit')).toHaveTextContent('Signed in');
  });

  it('offers to resend the link when the password is right but the address is unconfirmed', async () => {
    const fetchMock = api(
      json(403, {
        error: {
          code: 'email_not_confirmed',
          message: 'Confirm your email address first. We sent you a link when you signed up.',
        },
      }),
    );
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />);

    await user.click(await screen.findByTestId('login-submit'));
    expect(await screen.findByTestId('email-not-confirmed')).toHaveTextContent(
      /Confirm your email address first/,
    );

    await user.click(screen.getByTestId('resend-confirmation'));

    expect(await screen.findByText(/Check your inbox/)).toBeInTheDocument();
    const resent = fetchMock.mock.calls.find(([url]) =>
      String(url).includes('/confirmation/resend'),
    );
    expect(resent).toBeDefined();
  });

  it('says so when a confirmation link lands here', async () => {
    search = new URLSearchParams('confirmed=1');
    vi.stubGlobal('fetch', api(json(204, null)));
    renderWithProviders(<LoginPage />);

    expect(await screen.findByTestId('confirmation-succeeded')).toHaveTextContent(
      'Email confirmed. Sign in to continue.',
    );
  });

  it('scrubs the Supabase session a confirmation link leaves in the address bar', async () => {
    // A bearer token in a URL ends up in history, screenshots and shared links.
    search = new URLSearchParams('confirmed=1');
    window.history.replaceState(null, '', '/login?confirmed=1#access_token=secret&type=signup');
    vi.stubGlobal('fetch', api(json(204, null)));
    renderWithProviders(<LoginPage />);

    await screen.findByTestId('confirmation-succeeded');
    expect(window.location.hash).toBe('');
    expect(window.location.search).toBe('?confirmed=1');
  });

  it('explains a failed link in its own words and never renders the text it carried', async () => {
    window.history.replaceState(
      null,
      '',
      '/login#error=access_denied&error_code=otp_expired&error_description=Call+this+number',
    );
    vi.stubGlobal('fetch', api(json(204, null)));
    renderWithProviders(<LoginPage />);

    expect(await screen.findByTestId('confirmation-failed')).toHaveTextContent(
      /expired or was already used/,
    );
    expect(screen.queryByText(/Call this number/)).not.toBeInTheDocument();
  });
});
