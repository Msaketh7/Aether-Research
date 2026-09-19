/**
 * Capture the README's screenshots from the running application.
 *
 * Screenshots in a README go stale silently: the UI changes, the picture does
 * not, and nobody notices until a reader is looking at a screen that no longer
 * exists. So they are generated rather than taken - `make screenshots` drives
 * the real app in a real browser and overwrites every file, which makes
 * refreshing them a command instead of a chore.
 *
 * It runs against the app in mock mode (ADR 0009), because that is the only
 * mode with a deterministic corpus: the same seven runs, the same claims, the
 * same contradictions on every machine. The app's own demo banner is visible in
 * every shot and says so - a screenshot of fixture data presented as research
 * output would be exactly the fabrication this repository forbids.
 *
 *   node scripts/screenshots.mjs                 # against an already-running server
 *   node scripts/screenshots.mjs --port 3000
 */
import { mkdir, readdir, rm } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { chromium } from 'playwright';

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUT = path.join(REPO, 'docs', 'screenshots');

const args = process.argv.slice(2);
const port = Number(valueOf('--port') ?? process.env.SCREENSHOT_PORT ?? 3000);
// `localhost`, not `127.0.0.1`: the dev server's HMR socket refuses the literal
// address, and in Next 16 a failed HMR handshake leaves the page server-rendered
// but never hydrated - which photographs as an app with an empty body.
const baseURL = `http://localhost:${port}`;

function valueOf(flag) {
  const i = args.indexOf(flag);
  return i === -1 ? undefined : args[i + 1];
}

/** The run's own tab bar, so a source link named "Report" cannot match. */
function runTab(page, name) {
  return page.getByRole('navigation', { name: 'Research sections' }).getByRole('link', { name });
}

/**
 * The flagship fixture: the one completed deep run the whole UI is designed
 * against, reached by its title rather than by an id, because the id is a hash
 * of a seed string and would have to be recomputed here to be pasted in.
 */
const FLAGSHIP = 'AI inference infrastructure landscape';

/**
 * Each shot names the screen it proves. `prepare` leaves the page in the state
 * worth photographing; `settle` is what must be on screen before the shutter
 * opens, so a capture can never catch a loading skeleton.
 */
const SHOTS = [
  {
    file: 'dashboard.png',
    caption: 'Dashboard: every run, its cost, its findings',
    async prepare(page) {
      await page.goto(`${baseURL}/dashboard`);
    },
    async settle(page) {
      await page.getByTestId('run-row').first().waitFor({ state: 'visible' });
    },
  },
  {
    file: 'new-research.png',
    caption: 'Starting a run: the question, the mode, the bounds',
    async prepare(page) {
      await page.goto(`${baseURL}/research/new`);
      await page
        .getByTestId('question-input')
        .fill(
          'Compare the major AI inference infrastructure companies across pricing, technology, funding and risk.',
        );
    },
    async settle(page) {
      await page.getByTestId('start-research').waitFor({ state: 'visible' });
    },
  },
  {
    file: 'activity.png',
    caption: 'The agent trace: every node execution with the tool and model calls it made',
    async prepare(page) {
      await gotoFlagship(page);
      await runTab(page, 'Activity').click();
    },
    async settle(page) {
      // The agent trace, not the event feed: on a finished run the feed is
      // correctly empty ("this run has already finished"), and the trace is the
      // half that answers why the run did what it did.
      await page.getByTestId('agent-run').first().waitFor({ state: 'visible' });
      await page.getByTestId('tool-call').first().waitFor({ state: 'visible' });
    },
  },
  {
    file: 'sources.png',
    caption: 'Sources: provenance, credibility and duplicate clusters',
    async prepare(page) {
      await gotoFlagship(page);
      await runTab(page, 'Sources').click();
    },
    async settle(page) {
      await page.getByTestId('source-card').first().waitFor({ state: 'visible' });
    },
  },
  {
    file: 'evidence.png',
    caption: 'Evidence: claims with verbatim spans, contradictions recorded rather than resolved',
    async prepare(page) {
      await gotoFlagship(page);
      await runTab(page, 'Evidence').click();
    },
    async settle(page) {
      await page.getByTestId('contradiction-card').first().waitFor({ state: 'visible' });
      // Contradictions come first on the page and would fill the frame on their
      // own. The claims below them are the half that carries the verbatim spans,
      // so the shot is framed on the boundary between the two.
      await page.getByTestId('claim-card').first().scrollIntoViewIfNeeded();
      await page.getByTestId('evidence-row').first().waitFor({ state: 'visible' });
    },
  },
  {
    file: 'report.png',
    caption: 'The report: every [n] opens the source and the quote behind it',
    async prepare(page) {
      await gotoFlagship(page);
      await runTab(page, 'Report').click();
      await page.getByTestId('report-section').first().waitFor({ state: 'visible' });
      // The popover is the point of the shot: a citation that resolves to a
      // real span is the product's central promise, and a static report page
      // does not show it.
      await page.getByTestId('citation-marker').first().click();
    },
    async settle(page) {
      await page.getByTestId('citation-popover').waitFor({ state: 'visible' });
    },
  },
  {
    file: 'evaluations.png',
    caption: 'Evaluations: not measured is rendered as not measured, never as zero',
    async prepare(page) {
      await page.goto(`${baseURL}/evaluations`);
    },
    async settle(page) {
      await page.getByRole('heading', { name: 'Evaluations', level: 1 }).waitFor({
        state: 'visible',
      });
    },
  },
];

async function gotoFlagship(page) {
  await page.goto(`${baseURL}/dashboard`);
  await page.getByRole('link', { name: FLAGSHIP }).click();
  await page.locator('[data-status="completed"]').first().waitFor({ state: 'visible' });
}

async function main() {
  await mkdir(OUT, { recursive: true });

  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    colorScheme: 'dark',
    // The app renders relative timestamps ("26 minutes ago") and currency, both
    // of which are locale-dependent. Pinning them keeps a re-run's diff to the
    // pixels that actually changed.
    locale: 'en-GB',
    timezoneId: 'UTC',
    reducedMotion: 'reduce',
  });

  const page = await context.newPage();
  const failures = [];
  page.on('pageerror', (error) => failures.push(String(error)));

  // The dev server's own floating overlay is not part of the product, and it
  // sits in the corner of every frame. `addInitScript` reapplies this on each
  // navigation, which a single injected <style> would not survive.
  await page.addInitScript(() => {
    const install = () => {
      const hide = document.createElement('style');
      hide.textContent = 'nextjs-portal, [data-nextjs-toast] { display: none !important; }';
      (document.head ?? document.documentElement).append(hide);
    };
    // An init script runs before the document exists, so there is nothing to
    // append to yet on the first navigation of each page.
    if (document.documentElement) install();
    else document.addEventListener('DOMContentLoaded', install, { once: true });
  });

  const written = [];
  for (const shot of SHOTS) {
    process.stdout.write(`  ${shot.file} ... `);
    await shot.prepare(page);
    await shot.settle(page);
    // One frame after the last assertion, so a transition that has been started
    // has also finished.
    await page.waitForTimeout(400);
    await page.screenshot({ path: path.join(OUT, shot.file), fullPage: false });
    written.push(shot.file);
    process.stdout.write('ok\n');
  }

  await context.close();
  await browser.close();

  if (failures.length > 0) {
    // A screenshot of a page that threw is a screenshot of a broken product.
    console.error('\nthe page reported errors while being photographed:');
    for (const failure of failures) console.error(`  ${failure}`);
    process.exitCode = 1;
    return;
  }

  const stale = (await readdir(OUT)).filter(
    (name) => name.endsWith('.png') && !written.includes(name),
  );
  for (const name of stale) {
    await rm(path.join(OUT, name));
    console.log(`  removed stale ${name}`);
  }

  console.log(`\n${written.length} screenshots in docs/screenshots/`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
