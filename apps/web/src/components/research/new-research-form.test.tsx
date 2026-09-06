import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '@/test/render';
import { NewResearchForm } from './new-research-form';

const push = vi.fn();
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));

/**
 * The form is the product's entry point, so both halves of its contract are
 * tested: it must refuse to submit input the API would reject, and it must
 * surface a server-side field error the client did not know about.
 */

function mockCreateResponse() {
  return vi.fn().mockResolvedValue({
    ok: true,
    status: 202,
    statusText: 'Accepted',
    json: async () => ({ run_id: 'run-123', status: 'queued', events_url: '/x' }),
  });
}

beforeEach(() => {
  push.mockClear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('NewResearchForm', () => {
  it('blocks submission and explains why when the question is too short', async () => {
    const fetchMock = mockCreateResponse();
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    renderWithProviders(<NewResearchForm />);
    await user.type(screen.getByTestId('question-input'), 'too short');
    await user.click(screen.getByTestId('start-research'));

    expect(await screen.findByText(/at least 15 characters/i)).toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(push).not.toHaveBeenCalled();
  });

  it('submits a valid request and navigates to the new run', async () => {
    const fetchMock = mockCreateResponse();
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    renderWithProviders(
      <NewResearchForm initialQuestion="Compare the major AI inference infrastructure companies." />,
    );
    await user.click(screen.getByTestId('start-research'));

    await waitFor(() => expect(push).toHaveBeenCalledWith('/research/run-123'));

    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body)) as Record<string, unknown>;
    expect(body.mode).toBe('deep');
    expect(body.depth).toBe(3);
    expect(body.question).toBe('Compare the major AI inference infrastructure companies.');
  });

  it('omits depth when the run is a quick one', async () => {
    const fetchMock = mockCreateResponse();
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    renderWithProviders(
      <NewResearchForm initialQuestion="Compare the major AI inference infrastructure companies." />,
    );
    await user.click(screen.getByTestId('mode-quick'));
    await user.click(screen.getByTestId('start-research'));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body)) as Record<string, unknown>;
    expect(body.mode).toBe('quick');
    expect(body).not.toHaveProperty('depth');
  });

  it('hides the depth selector for quick runs, which do a single pass', async () => {
    const user = userEvent.setup();
    renderWithProviders(<NewResearchForm />);

    expect(screen.getByTestId('depth-3')).toBeInTheDocument();
    await user.click(screen.getByTestId('mode-quick'));
    expect(screen.queryByTestId('depth-3')).not.toBeInTheDocument();
  });

  it('normalises a pasted URL into a bare domain filter', async () => {
    const user = userEvent.setup();
    renderWithProviders(<NewResearchForm />);

    await user.type(screen.getByTestId('domain-input'), 'https://www.sec.gov/edgar{Enter}');
    expect(screen.getByText('sec.gov')).toBeInTheDocument();
  });

  it('shows a server-side field error the client did not anticipate', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        statusText: 'Unprocessable Entity',
        json: async () => ({
          error: {
            code: 'validation_failed',
            message: 'Check the highlighted fields.',
            details: { question: ['This topic is not supported yet.'] },
          },
        }),
      }),
    );
    const user = userEvent.setup();

    renderWithProviders(
      <NewResearchForm initialQuestion="Compare the major AI inference infrastructure companies." />,
    );
    await user.click(screen.getByTestId('start-research'));

    expect(await screen.findByText('This topic is not supported yet.')).toBeInTheDocument();
    expect(push).not.toHaveBeenCalled();
  });
});
