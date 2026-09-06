import type { Citation, ReportSection } from '@aether/shared-types';
import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { renderWithProviders } from '@/test/render';
import { ReportSectionBody } from './report-view';

/**
 * The report is where the product's central promise is kept: every factual
 * statement resolves to a source and the exact quote behind it. These tests
 * cover that path, including the failure case where a marker has no citation.
 */

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

function section(content: string): ReportSection {
  return {
    id: 'section-1',
    report_id: 'report-1',
    kind: 'key_findings',
    heading: 'Key Findings',
    ordinal: 1,
    content_md: content,
  };
}

describe('ReportSectionBody', () => {
  it('renders prose with an interactive citation marker', () => {
    renderWithProviders(
      <ReportSectionBody
        section={section('Serverless inference is priced per token [1].')}
        citations={[citation]}
      />,
    );

    expect(screen.getByText(/Serverless inference is priced per token/)).toBeInTheDocument();
    const marker = screen.getByTestId('citation-marker');
    expect(marker).toHaveAttribute('data-ordinal', '1');
    expect(marker).toHaveAccessibleName(/Fireworks AI pricing page/);
  });

  it('reveals the source and the verbatim quote when a citation is opened', async () => {
    const user = userEvent.setup();
    renderWithProviders(
      <ReportSectionBody
        section={section('Serverless inference is priced per token [1].')}
        citations={[citation]}
      />,
    );

    await user.click(screen.getByTestId('citation-marker'));

    const popover = await screen.findByTestId('citation-popover');
    expect(
      within(popover).getByText(/billing is per million input and output tokens/),
    ).toBeInTheDocument();
    expect(
      within(popover).getByRole('link', { name: /Fireworks AI pricing page/ }),
    ).toHaveAttribute('href', 'https://fireworks.ai/pricing');
  });

  it('flags a marker that cannot be resolved instead of hiding it', () => {
    renderWithProviders(
      <ReportSectionBody
        section={section('An unsupported assertion [9].')}
        citations={[citation]}
      />,
    );

    expect(screen.getByTestId('citation-unresolved')).toBeInTheDocument();
    expect(screen.queryByTestId('citation-marker')).not.toBeInTheDocument();
  });

  it('renders lists and tables from the section markdown', () => {
    renderWithProviders(
      <ReportSectionBody
        section={section(
          '1. First finding [1]\n2. Second finding\n\n| Axis | Leader |\n|---|---|\n| Latency | Custom silicon |',
        )}
        citations={[citation]}
      />,
    );

    expect(screen.getByRole('list')).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Axis' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: 'Custom silicon' })).toBeInTheDocument();
  });

  it('does not execute or interpret HTML found in source-derived text', () => {
    const { container } = renderWithProviders(
      <ReportSectionBody
        section={section('The page said <img src=x onerror=alert(1)> which is not markup.')}
        citations={[]}
      />,
    );

    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByText(/<img src=x onerror=alert\(1\)>/)).toBeInTheDocument();
  });
});
