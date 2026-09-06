import { RUN_STATUSES } from '@aether/shared-types';
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ClaimStatusBadge, ResolutionBadge, RunStatusBadge } from './badges';

describe('RunStatusBadge', () => {
  it('renders a label for every status in the contract', () => {
    for (const status of RUN_STATUSES) {
      const { unmount } = render(<RunStatusBadge status={status} />);
      expect(screen.getByText(/\w/)).toBeInTheDocument();
      unmount();
    }
  });

  it('animates only while the run is still working', () => {
    const { rerender } = render(<RunStatusBadge status="researching" />);
    expect(screen.getByTestId('status-pulse')).toBeInTheDocument();

    rerender(<RunStatusBadge status="completed" />);
    expect(screen.queryByTestId('status-pulse')).not.toBeInTheDocument();
  });

  it('does not present a cancelled run as a failure', () => {
    render(<RunStatusBadge status="cancelled" />);
    expect(screen.getByText('Cancelled')).toBeInTheDocument();
    expect(screen.queryByText(/failed/i)).not.toBeInTheDocument();
  });
});

describe('ClaimStatusBadge', () => {
  it('distinguishes an unverified candidate from a verified claim', () => {
    const { rerender } = render(<ClaimStatusBadge status="candidate" />);
    expect(screen.getByText('Candidate')).toBeInTheDocument();

    rerender(<ClaimStatusBadge status="verified" />);
    expect(screen.getByText('Verified')).toBeInTheDocument();
  });
});

describe('ResolutionBadge', () => {
  it('says plainly when a contradiction is unresolved', () => {
    render(<ResolutionBadge resolution="unresolved" />);
    expect(screen.getByText('Unresolved')).toBeInTheDocument();
  });

  it('names which source won when one did', () => {
    render(<ResolutionBadge resolution="resolved_a" />);
    expect(screen.getByText(/source A/i)).toBeInTheDocument();
  });
});
