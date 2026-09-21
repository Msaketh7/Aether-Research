import { RESEARCH_STAGES } from '@aether/shared-types';
import { expect, test, type Page } from '@playwright/test';

/**
 * Structural accessibility and responsive checks.
 *
 * Not a substitute for an audit - these pin the properties that regress most
 * easily as a page grows: one h1 per page, reachable navigation, labelled
 * controls, and no horizontal scroll on a phone.
 */

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

const PAGES = ['/', '/dashboard', '/research/new', '/evaluations', '/settings'] as const;

for (const path of PAGES) {
  test(`${path} has exactly one level-1 heading`, async ({ page }) => {
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1 })).toHaveCount(1);
  });
}

test('the new-research form labels every control', async ({ page }) => {
  await page.goto('/research/new');

  await expect(page.getByLabel('Research question')).toBeVisible();
  await expect(page.getByRole('radiogroup', { name: 'Research mode' })).toBeVisible();
  await expect(page.getByLabel('Domains')).toBeVisible();
  await expect(page.getByLabel('Published after')).toBeVisible();
});

test('the form is operable by keyboard alone', async ({ page }) => {
  await page.goto('/research/new');

  await page.getByTestId('question-input').focus();
  await page.keyboard.type('Compare inference providers on pricing and latency guarantees.');
  await page.getByTestId('mode-quick').press('Enter');

  await expect(page.getByTestId('mode-quick')).toHaveAttribute('aria-checked', 'true');
});

for (const path of ['/', '/dashboard'] as const) {
  test(`${path} does not scroll horizontally on a phone`, async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    await page.goto(path);
    await expect(page.getByTestId('run-row').first()).toBeVisible();

    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    // A couple of pixels of rounding is tolerable; a scrolling page is not.
    expect(overflow).toBeLessThanOrEqual(2);
  });
}

test('a run does not scroll horizontally on a phone', async ({ page }) => {
  // The one page in the product that stacks a conversation, a composer and a
  // two-column grid of cards. The loop above cannot cover it: it needs a run id,
  // which only the history has.
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/dashboard');
  await historyLink(page, 'AI inference infrastructure landscape').click();
  await expect(page.getByTestId('answer-body')).toBeVisible();

  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  expect(overflow).toBeLessThanOrEqual(2);
});

test('the progress checklist is a named list with one entry per stage', async ({ page }) => {
  await page.goto('/dashboard');
  await historyLink(page, 'AI inference infrastructure landscape').click();

  const checklist = page.getByRole('list', { name: 'Research progress' }).first();
  await expect(checklist).toBeVisible();
  // One row per declared stage, answering included.
  await expect(checklist.getByRole('listitem')).toHaveCount(RESEARCH_STAGES.length);
});

test('a finished run explains an empty feed instead of showing a stale live region', async ({
  page,
}) => {
  await page.goto('/dashboard');
  await historyLink(page, 'AI inference infrastructure landscape').click();

  await expect(page.getByText(/This run has finished/)).toBeVisible();
  await expect(page.getByRole('log', { name: 'Research activity' })).toHaveCount(0);
});
