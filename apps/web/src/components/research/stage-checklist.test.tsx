import type { StageProgress } from '@aether/shared-types';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { StageChecklist } from './stage-checklist';

const stages: StageProgress[] = [
  { stage: 'planning', state: 'done', detail: '7 subtasks', agent: 'planner' },
  { stage: 'searching', state: 'done', detail: '12 queries', agent: 'researcher' },
  { stage: 'reading', state: 'active', detail: '9/14 sources', agent: 'researcher' },
  { stage: 'extracting', state: 'pending', detail: null, agent: 'evidence_extractor' },
  { stage: 'verifying', state: 'pending', detail: null, agent: 'verifier' },
  { stage: 'contradictions', state: 'pending', detail: null, agent: 'critic' },
  { stage: 'writing', state: 'pending', detail: null, agent: 'synthesizer' },
];

describe('StageChecklist', () => {
  it('renders every stage with its state exposed for assistive technology', () => {
    render(<StageChecklist stages={stages} />);

    const items = screen.getAllByRole('listitem');
    expect(items).toHaveLength(7);
    expect(items[2]).toHaveAttribute('aria-current', 'step');
    expect(items[0]).not.toHaveAttribute('aria-current');
  });

  it('shows counted detail next to a stage that has produced something', () => {
    render(<StageChecklist stages={stages} />);

    expect(screen.getByText('9/14 sources')).toBeInTheDocument();
    expect(screen.getByText('7 subtasks')).toBeInTheDocument();
  });

  it('uses the human label rather than the raw stage key', () => {
    render(<StageChecklist stages={stages} />);

    expect(screen.getByText('Detecting contradictions')).toBeInTheDocument();
    expect(screen.queryByText('contradictions')).not.toBeInTheDocument();
  });

  it('marks a failed stage rather than showing it as still running', () => {
    render(
      <StageChecklist
        stages={stages.map((stage) =>
          stage.stage === 'reading' ? { ...stage, state: 'failed' } : stage,
        )}
      />,
    );

    const failed = screen.getAllByRole('listitem')[2];
    expect(failed).toHaveAttribute('data-state', 'failed');
    expect(failed).not.toHaveAttribute('aria-current');
  });
});
