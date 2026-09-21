import { expect, test, type Page } from '@playwright/test';

/** The run's own tab bar, so a source link named "Report" cannot match. */

/**
 * A run link in the dashboard's history list.
 *
 * The navigation rail also lists recent runs, so the page holds two links to
 * the same run on purpose. A bare `getByRole('link', { name })` matches both;
 * these tests are about opening a run from the history list, so they say so.
 */
function historyLink(page: Page, name: string) {
  return page.getByRole('region', { name: 'Research history' }).getByRole('link', { name });
}
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

test('a user can sign in and reach the question box', async ({ page }) => {
  await page.goto('/login');
  await page.getByTestId('login-submit').click();

  // Signing in lands on the home screen, which is the question box - not the
  // dashboard. Starting a piece of research is what someone came here to do.
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole('heading', { name: /What would you like researched/ })).toBeVisible();
  await expect(page.getByTestId('ask-input')).toBeVisible();
  // Fixture data must always announce itself.
  await expect(page.getByTestId('demo-banner')).toBeVisible();
});

test('a new user can register, arrive signed in, and sign out again', async ({ page }) => {
  // The first acceptance criterion, and the one Phase 20 made real. Sign-out is
  // the half worth a journey: it used to be a link to /login, which left the
  // session alive on the server and one person's research in the browser cache
  // for whoever used the machine next.
  await page.goto('/login');
  await page.getByRole('link', { name: 'Create one' }).click();

  await expect(page).toHaveURL(/\/register$/);
  await page.getByLabel('Email').fill('ada@example.com');
  await page.getByLabel('Password').fill('correct-horse-battery-staple');
  await page.getByTestId('register-submit').click();

  await expect(page).toHaveURL(/\/$/);

  const logout = page.waitForResponse(
    (response) => response.url().includes('/auth/logout') && response.request().method() === 'POST',
  );
  await page.getByRole('button', { name: 'Account menu' }).click();
  await page.getByTestId('sign-out').click();

  await logout;
  await expect(page).toHaveURL(/\/login$/);
});

test('registration renders the API refusal beside the field that caused it', async ({ page }) => {
  // The password policy lives on the server, so the page's job is to put the
  // server's answer where the user is typing rather than to hold a second copy
  // of the rules.
  await page.goto('/register');

  await page.getByLabel('Email').fill('ada@example.com');
  await page.getByLabel('Password').fill('short');
  await page.getByTestId('register-submit').click();

  await expect(page.getByText(/at least 12 characters/i)).toBeVisible();
  await expect(page).toHaveURL(/\/register$/);
});

test('the dashboard lists research history with measured totals', async ({ page }) => {
  await page.goto('/dashboard');

  await expect(page.getByTestId('run-row').first()).toBeVisible();
  await expect(page.getByText('Research runs')).toBeVisible();
  await expect(page.getByText('Sources gathered')).toBeVisible();

  const rows = await page.getByTestId('run-row').count();
  expect(rows).toBeGreaterThan(3);
});

test('the home question box starts a run without leaving the page', async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto('/');

  // Depth and filters open in a card over the question, not on another screen:
  // leaving the page to add a date filter means abandoning a half-typed
  // question, which is the whole reason this control is not a link.
  await page.getByRole('button', { name: /Filters and depth/ }).click();
  const filters = page.getByTestId('composer-filters');
  await expect(filters).toBeVisible();
  await expect(page).toHaveURL(/\/$/);

  // Each setting is a collapsed row that states its own value; opening one is
  // a deliberate act, and only one is open at a time.
  await expect(filters.getByTestId('depth-4')).toBeHidden();
  await filters.getByRole('button', { name: /^Depth/ }).click();
  await filters.getByTestId('depth-4').click();

  await filters.getByRole('button', { name: /^Domains/ }).click();
  await expect(filters.getByTestId('depth-4')).toBeHidden();
  await filters.getByTestId('composer-domain-input').fill('sec.gov');
  await filters.getByRole('button', { name: 'Add', exact: true }).click();
  // The chip's own remove control, not the text: "sec.gov" now appears in the
  // collapsed row's summary and in the input's placeholder as well.
  await expect(filters.getByRole('button', { name: 'Remove sec.gov' })).toBeVisible();

  // The count on the trigger is how a filter you have scrolled past stays
  // visible; a run that quietly searched one domain looks like a run that
  // found nothing.
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: /Filters and depth, 2 applied/ })).toBeVisible();

  await page
    .getByTestId('ask-input')
    .fill('Compare the major AI inference providers on pricing, latency and funding.');
  await page.getByTestId('ask-submit').click();

  await expect(page).toHaveURL(/\/research\/[0-9a-f-]{36}$/);
});

test('a suggestion fills the question box instead of navigating away', async ({ page }) => {
  await page.goto('/');

  await page.getByRole('button', { name: /Competitive landscape/ }).click();

  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId('ask-input')).toHaveValue(/inference infrastructure companies/);
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

  // The answer arrives before the report, as prose rather than as a bar. Its
  // body appearing at all is the assertion that matters: it can only be there
  // because `answer_delta` events were received and assembled.
  await expect(page.getByTestId('answer-body')).toBeVisible({ timeout: 90_000 });
  await expect(page.getByTestId('answer-body')).not.toBeEmpty();

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
  await historyLink(page, 'AI inference infrastructure landscape').click();

  await expect(page.locator('[data-status="completed"]').first()).toBeVisible();

  // The conversation: the question that was asked, the answer under it, and the
  // box for the next one under that. Nobody streamed this answer to this
  // browser - it is read back from the run, which is the path every reader who
  // arrives after a run has finished takes.
  await expect(page.getByTestId('run-question')).toBeVisible();
  await expect(page.getByTestId('answer-body')).toBeVisible();
  await expect(page.getByTestId('answer-body')).toHaveAttribute('data-streaming', 'false');
  await expect(page.getByTestId('follow-up-input')).toBeEnabled();

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
  await historyLink(page, 'Retrieval chunking strategies').click();

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
  await historyLink(page, 'AI inference infrastructure landscape').click();
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
