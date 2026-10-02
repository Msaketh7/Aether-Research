import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { resetRefreshState } from '@/lib/auth/refresh';
import ResumePage from './page';

const replace = vi.fn();
let search = new URLSearchParams();
vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => search,
}));

/**
 * The page a guarded route sends someone to when their access token is gone.
 * Its whole job is choosing between three destinations, and choosing the
 * sign-in form for an outage is the mistake it exists not to make.
 */

function refreshAnswers(...statuses: number[]) {
  const fn = vi.fn();
  for (const status of statuses) {
    fn.mockResolvedValueOnce({ ok: status < 300, status, json: async () => ({}) });
  }
  vi.stubGlobal('fetch', fn);
  return fn;
}

beforeEach(() => {
  replace.mockClear();
  resetRefreshState();
  search = new URLSearchParams({ next: '/research/abc?tab=evidence' });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ResumePage', () => {
  it('renews the session and continues to where the visitor was going', async () => {
    refreshAnswers(200);
    render(<ResumePage />);

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/research/abc?tab=evidence'));
  });

  it('sends a visitor with nothing to renew to sign in, carrying the destination', async () => {
    refreshAnswers(401);
    render(<ResumePage />);

    await waitFor(() =>
      expect(replace).toHaveBeenCalledWith(
        `/login?${new URLSearchParams({ next: '/research/abc?tab=evidence' }).toString()}`,
      ),
    );
  });

  it('never forwards off this origin', async () => {
    search = new URLSearchParams({ next: '//evil.example/phish' });
    refreshAnswers(200);
    render(<ResumePage />);

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/'));
  });

  it('stays put when the API cannot be reached, and tries again on request', async () => {
    // An outage is not an answer about the session; the sign-in form would
    // tell the visitor the wrong thing.
    const fetchMock = refreshAnswers(503, 200);
    render(<ResumePage />);

    const retry = await screen.findByRole('button', { name: 'Try again' });
    expect(replace).not.toHaveBeenCalled();

    await userEvent.click(retry);

    await waitFor(() => expect(replace).toHaveBeenCalledWith('/research/abc?tab=evidence'));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('says what it is doing while it asks', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise(() => {})),
    );
    render(<ResumePage />);

    expect(screen.getByRole('status')).toHaveTextContent('Restoring your session');
  });
});
