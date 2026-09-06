import { defineConfig, devices } from '@playwright/test';

/**
 * Smoke coverage for the Phase 1 prototype. The suite runs against the app in
 * mock mode (ADR 0009): a real Next.js server, real HTTP, deterministic data.
 * When the FastAPI backend lands, the same specs run against it by changing
 * NEXT_PUBLIC_API_MODE.
 */
const PORT = Number(process.env.PLAYWRIGHT_PORT ?? 3100);
const baseURL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : [['list']],
  timeout: 60_000,
  expect: { timeout: 15_000 },
  use: {
    baseURL,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `npm run build && npx next start -p ${PORT}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    env: {
      NEXT_PUBLIC_API_MODE: 'mock',
      // Compress the simulated run so the smoke suite stays fast.
      NEXT_PUBLIC_MOCK_SPEED: '12',
    },
  },
});
