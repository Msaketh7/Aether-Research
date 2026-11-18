import { expect, test, type Page } from '@playwright/test';

/**
 * The whole product stands on the sign-in stage.
 *
 * One palette, always dark, taken from the sign-in pages; the signed-in pages
 * stand on a painted still of the same light rather than the video. These pin
 * the properties that are easy to lose without anything else failing: that
 * an OS asking for light changes nothing, that no video plays behind the
 * working pages, that the glass actually blurs, and that the sign-in form is
 * in the first HTML and does not move once painted.
 */

/** `--ink` resolved through the page's own colour parser, so this compares like with like. */
async function inkAndBody(page: Page) {
  return page.evaluate(() => {
    const probe = document.createElement('div');
    probe.style.backgroundColor = 'var(--ink)';
    document.body.append(probe);
    const ink = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return {
      ink,
      body: getComputedStyle(document.body).backgroundColor,
      scheme: getComputedStyle(document.documentElement).colorScheme,
    };
  });
}

for (const path of ['/login', '/', '/dashboard'] as const) {
  test(`${path} is the dark stage even when the OS asks for light`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'light' });
    await page.goto(path);
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();

    const { ink, body, scheme } = await inkAndBody(page);
    expect(scheme).toBe('dark');
    expect(body).toBe(ink);
    // One theme, so nothing offers to change it.
    await expect(page.getByRole('button', { name: /colour theme/i })).toHaveCount(0);
  });
}

test('the working pages stand on painted light, never a video', async ({ page }) => {
  // A video behind every report would be a download per visit and a decoder
  // running for as long as the page is open; the sign-in pages are the only
  // place it plays.
  await page.goto('/');
  await expect(page.getByTestId('ask-input')).toBeVisible();

  await expect(page.locator('video')).toHaveCount(0);
  await expect(page.locator('.backdrop-light')).toHaveCount(1);
});

test('the sticky glass really blurs what scrolls under it', async ({ page }) => {
  // Written as an unprefixed declaration followed by a `-webkit-` copy, the
  // build's CSS optimiser kept only the prefix - which Chrome ignores - and
  // the bars shipped with no blur at all while the source said otherwise.
  await page.goto('/dashboard');
  await expect(page.getByTestId('run-row').first()).toBeVisible();

  const blur = await page.evaluate(
    () => getComputedStyle(document.querySelector('header.glass') as Element).backdropFilter,
  );
  expect(blur).toContain('blur');
});

test('the sign-in form is in the first HTML rather than swapped in after hydration', async ({
  request,
}) => {
  // Behind a Suspense boundary, a prerendered page ships the fallback and the
  // form arrives with the JavaScript: a skeleton flash on the page everybody
  // sees first, and a page that cannot be used until the bundle has run.
  const html = await (await request.get('/login')).text();

  expect(html).toContain('data-testid="login-submit"');
  expect(html).toContain('id="email"');
  expect(html).toContain('id="password"');
});

for (const path of ['/login', '/register'] as const) {
  test(`${path} does not move once it has painted`, async ({ page }) => {
    await page.addInitScript(() => {
      const shifts: number[] = [];
      (window as unknown as { __shifts: number[] }).__shifts = shifts;
      new PerformanceObserver((list) => {
        for (const entry of list.getEntries() as (PerformanceEntry & {
          value: number;
          hadRecentInput: boolean;
        })[]) {
          if (!entry.hadRecentInput) shifts.push(entry.value);
        }
      }).observe({ type: 'layout-shift', buffered: true });
    });

    await page.goto(path);
    // The single sign-on buttons are the last thing to arrive: they wait on a
    // request, and their placeholder has to be the height they replace it at.
    await expect(page.getByTestId('sso-google')).toBeVisible();
    await page.waitForTimeout(500);

    const shift = await page.evaluate(() =>
      (window as unknown as { __shifts: number[] }).__shifts.reduce((sum, v) => sum + v, 0),
    );
    // The old placeholder left out the divider row and measured 0.003-0.005.
    expect(shift).toBeLessThan(0.001);
  });
}
