import type { Citation, ResearchRun, StageProgress } from '@aether/shared-types';
import { screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { renderWithProviders } from '@/test/render';
import type { StreamedAnswer } from '@/lib/sse/use-research-events';
import { AnswerPanel } from './answer-panel';

/**
 * The answer is the first thing a reader sees and, most of the time, the only
 * thing they read. These cover what it must never do: look finished while it is
 * still arriving, look empty while the run is still working, and resolve a
 * citation nobody has checked.
 */

const { mockContext } = vi.hoisted(() => ({ mockContext: vi.fn() }));

vi.mock('./run-context', () => ({ useRunContext: mockContext }));

const citation: Citation = {
  id: 'citation-1',
  ordinal: 1,
  report_section_id: 'section-1',
  claim_id: 'claim-1',
  source_id: 'source-1',
  source_title: 'Fireworks AI pricing page',
  source_url: 'https://fireworks.ai/pricing',
  source_publisher: 'Fireworks AI',
  quote: 'billing is per million input and output tokens',
  confidence: 0.94,
};

const stages: StageProgress[] = [
  { stage: 'planning', state: 'done', detail: '3 subtasks', agent: 'planner' },
  { stage: 'reading', state: 'active', detail: '7/12 sources', agent: 'researcher' },
  { stage: 'writing', state: 'pending', detail: null, agent: 'synthesizer' },
];

function context({
  status = 'researching',
  isLive = true,
  answerPending = true,
}: {
  status?: ResearchRun['status'];
  isLive?: boolean;
  answerPending?: boolean;
} = {}) {
  mockContext.mockReturnValue({
    run: { status, coverage_caveat: null } as ResearchRun,
    stages,
    isLive,
    answerPending,
  });
}

function answer(overrides: Partial<StreamedAnswer> = {}): StreamedAnswer {
  return { text: '', streaming: false, complete: false, truncated: false, ...overrides };
}

describe('AnswerPanel', () => {
  it('says what the run is doing rather than showing an empty answer', () => {
    context();

    renderWithProviders(<AnswerPanel answer={answer()} />);

    // In words, with the count it has actually reached. A bare spinner is
    // indistinguishable from a hung page.
    expect(screen.getByTestId('answer-working')).toHaveTextContent(
      'Reading sources · 7/12 sources',
    );
  });

  it('marks a partial answer as still arriving', () => {
    context();

    renderWithProviders(
      <AnswerPanel answer={answer({ text: 'Inference is priced per token', streaming: true })} />,
    );

    // Both, and both matter: a partial answer that looks finished is one
    // somebody will act on.
    expect(screen.getByTestId('answer-body')).toHaveAttribute('data-streaming', 'true');
    expect(screen.getByTestId('answer-caret')).toBeInTheDocument();
  });

  it('drops the caret once the answer has settled', () => {
    context({ isLive: false, answerPending: false, status: 'completed' });

    renderWithProviders(
      <AnswerPanel answer={answer({ text: 'Inference is priced per token.', complete: true })} />,
    );

    expect(screen.getByTestId('answer-body')).toHaveAttribute('data-streaming', 'false');
    expect(screen.queryByTestId('answer-caret')).not.toBeInTheDocument();
  });

  it('resolves a citation against the report when there is one', () => {
    context({ isLive: false, answerPending: false, status: 'completed' });

    renderWithProviders(
      <AnswerPanel
        answer={answer({ text: 'Inference is priced per token [1].', complete: true })}
        citations={[citation]}
      />,
    );

    expect(screen.getByTestId('citation-marker')).toHaveAttribute('data-ordinal', '1');
  });

  it('shows a marker as unresolved while there is no report behind it', () => {
    context();

    renderWithProviders(
      <AnswerPanel
        answer={answer({ text: 'Inference is priced per token [1].', streaming: true })}
      />,
    );

    // The normal state of a streaming answer: the report is still being written,
    // so nothing has checked the chain yet. Hiding the marker would be the
    // renderer deciding a citation had been verified.
    expect(screen.getByTestId('citation-unresolved')).toBeInTheDocument();
  });

  it('says the answer stops early when the model reached its ceiling', () => {
    context({ isLive: false, answerPending: false, status: 'completed' });

    renderWithProviders(
      <AnswerPanel
        answer={answer({ text: 'It stops mid-sen', complete: true, truncated: true })}
      />,
    );

    expect(screen.getByTestId('answer-truncated')).toBeInTheDocument();
  });

  it('explains a finished run that never answered rather than showing nothing', () => {
    context({ isLive: false, answerPending: false, status: 'failed' });

    renderWithProviders(<AnswerPanel answer={answer()} />);

    expect(screen.getByTestId('answer-absent')).toHaveTextContent(/stopped before it could answer/);
  });
});
