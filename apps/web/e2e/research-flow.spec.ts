import { expect, test } from '@playwright/test';

/** The run's own tab bar, so a source link named "Report" cannot match. */
function runTab(page: import('@playwright/test').Page, name: string) {
  return page.getByRole('navigation', { name: 'Research sections' }).getByRole('link', { name });
}

/**
 * The end-to-end journey from the acceptance criteria: sign in, start a run,
 * watch the agents work, then inspect sources, evidence and the report.
 *
 * Runs against the app in mock mode (ADR 0009) with the timeline sped up, so
 * it exercises real HTTP, a real `text/event-stream` and the real client code -
 * only the research itself is simulated. The same specs run against the FastAPI
 * backend in Phase 2 by changing NEXT_PUBLIC_API_MODE.
 */

test('a user can sign in and reach the dashboard', async ({ page }) => {
  await page.goto('/login');
  await page.getByTestId('login-submit').click();

  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByRole('heading', { name: 'Dashboard', level: 1 })).toBeVisible();
  // Fixture data must always announce itself.
  await expect(page.getByTestId('demo-banner')).toBeVisible();
});

test('the dashboard lists research history with measured totals', async ({ page }) => {
  await page.goto('/dashboard');

  await expect(page.getByTestId('run-row').first()).toBeVisible();
  await expect(page.getByText('Research runs')).toBeVisible();
  await expect(page.getByText('Sources gathered')).toBeVisible();

  const rows = await page.getByTestId('run-row').count();
  expect(rows).toBeGreaterThan(3);
});

test('validation blocks an unusable research question', async ({ page }) => {
  await page.goto('/research/new');

  await page.getByTestId('question-input').fill('too short');
  await page.getByTestId('start-research').click();

  await expect(page.getByText(/at least 15 characters/i)).toBeVisible();
  await expect(page).toHaveURL(/\/research\/new/);
});

test('a new run streams progress and produces a validated report', async ({ page }) => {
  test.setTimeout(120_000);

  await page.goto('/research/new');
  await page
    .getByTestId('question-input')
    .fill(
      'Compare the major AI inference infrastructure companies across pricing, technology, funding and risk.',
    );
  await page.getByTestId('start-research').click();

  // 202 Accepted: the run is queued and the user lands on its page immediately.
  await expect(page).toHaveURL(/\/research\/[0-9a-f-]{36}$/);
  await expect(page.getByTestId('stream-indicator')).toBeVisible();

  // The checklist is driven by the event stream.
  const planning = page.locator('[data-stage="planning"]');
  await expect(planning).toHaveAttribute('data-state', /active|done/, { timeout: 30_000 });
  await expect(planning).toHaveAttribute('data-state', 'done', { timeout: 60_000 });

  // Sources are announced as they are found.
  await expect(page.locator('[data-event-type="source_found"]').first()).toBeVisible({
    timeout: 60_000,
  });

  // The run reaches a terminal state and the report becomes available.
  await expect(page.locator('[data-status="completed"]').first()).toBeVisible({
    timeout: 90_000,
  });

  // The feed is a polite live region while the run is producing events.
  await expect(page.getByRole('log', { name: 'Research activity' })).toHaveAttribute(
    'aria-live',
    'polite',
  );

  await runTab(page, 'Report').click();
  await expect(page.getByText(/^\d+ of \d+ citations validated$/)).toBeVisible({
    timeout: 30_000,
  });
  await expect(page.getByTestId('report-section').first()).toBeVisible();
});

test('a completed run exposes sources, evidence and citations', async ({ page }) => {
  await page.goto('/dashboard');
  await page.getByRole('link', { name: 'AI inference infrastructure landscape' }).click();

  await expect(page.locator('[data-status="completed"]').first()).toBeVisible();

  // Sources: filterable, with provenance on every card.
  await runTab(page, 'Sources').click();
  await expect(page.getByTestId('source-card').first()).toBeVisible();
  await expect(page.getByText(/Accessed/).first()).toBeVisible();

  await page.getByTestId('filter-sec').click();
  await expect(page.getByTestId('source-card')).toHaveCount(1);

  // Evidence: contradictions first, then claims with their verbatim spans.
  await runTab(page, 'Evidence').click();
  await expect(page.getByTestId('contradiction-card').first()).toBeVisible();
  await expect(page.getByText(/Likely reason \(hypothesis\)/).first()).toBeVisible();
  await expect(page.getByTestId('claim-card').first()).toBeVisible();
  await expect(page.getByTestId('evidence-row').first()).toBeVisible();

  // Report: every [n] resolves to a source and the quote behind it.
  await runTab(page, 'Report').click();
  const citation = page.getByTestId('citation-marker').first();
  await expect(citation).toBeVisible();
  await citation.click();

  const popover = page.getByTestId('citation-popover');
  await expect(popover).toBeVisible();
  await expect(popover.getByRole('link')).toHaveAttribute('href', /^https?:\/\//);
});

test('a failed run explains itself instead of showing an empty report', async ({ page }) => {
  await page.goto('/dashboard');
  await page.getByRole('link', { name: 'Retrieval chunking strategies' }).click();

  await expect(page.locator('[data-status="failed"]').first()).toBeVisible();
  await expect(page.getByText(/search_provider_unavailable/)).toBeVisible();

  await runTab(page, 'Report').click();
  await expect(page.getByText('No report was produced')).toBeVisible();
});

test('a user can cancel a running research job and the run says so', async ({ page }) => {
  // Phase 19 names user cancellation as a scenario that must be covered. The
  // backend half is a worker test; this is the half a user performs - and the
  // control only exists while the run is live, so it has to be a live run.
  test.setTimeout(120_000);

  await page.goto('/research/new');
  await page
    .getByTestId('question-input')
    .fill('Compare inference providers on published GPU-hour pricing and committed capacity.');
  await page.getByTestId('start-research').click();

  await expect(page).toHaveURL(/\/research\/[0-9a-f-]{36}$/);
  const cancel = page.getByTestId('cancel-run');
  await expect(cancel).toBeVisible({ timeout: 30_000 });
  await cancel.click();

  await expect(page.locator('[data-status="cancelled"]').first()).toBeVisible({
    timeout: 30_000,
  });
  // The control goes with the run: there is nothing left to cancel.
  await expect(cancel).toBeHidden();

  // Cancelling is not a rollback. What the run had already gathered stays, and
  // the report page explains its absence rather than showing an empty one.
  await runTab(page, 'Report').click();
  await expect(page.getByText('No report was produced')).toBeVisible();
  await expect(
    page.getByText(/sources and evidence it did gather are still available/i),
  ).toBeVisible();
});

test('the activity trace shows agents, tool calls and model calls', async ({ page }) => {
  await page.goto('/dashboard');
  await page.getByRole('link', { name: 'AI inference infrastructure landscape' }).click();
  await runTab(page, 'Activity').click();

  await expect(page.getByTestId('agent-run').first()).toBeVisible();
  await expect(page.getByTestId('tool-call').first()).toBeVisible();
  await expect(page.getByTestId('llm-call').first()).toBeVisible();
  await expect(page.getByText('Citation validator').first()).toBeVisible();
});

test('the evaluations page states that its figures are not measured', async ({ page }) => {
  await page.goto('/evaluations');

  await expect(page.getByRole('heading', { name: 'Evaluations', level: 1 })).toBeVisible();
  await expect(page.getByText(/These are fixture values/)).toBeVisible();
  await expect(page.getByTestId('metric-card').first()).toBeVisible();
  await expect(page.getByTestId('eval-case').first()).toBeVisible();
});

test('settings shows the transport mode and never a secret', async ({ page }) => {
  await page.goto('/settings');

  await expect(page.getByText('Base URL')).toBeVisible();
  await expect(page.getByText('/api/mock/v1')).toBeVisible();
  await expect(page.getByText(/never sent to the browser/i)).toBeVisible();
});
