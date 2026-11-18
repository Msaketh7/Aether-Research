import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  // Resolves the `@/*` alias from tsconfig.json natively.
  resolve: { tsconfigPaths: true },
  test: {
    environment: 'jsdom',
    // Threads rather than forks: measurably faster here, and forks cannot hand
    // off to workers at all while the repository lives under a path containing
    // a space (Windows), which it does.
    pool: 'threads',
    // Vitest defaults to one worker per core. On a laptop CPU that is being
    // downclocked, spinning up that many jsdom environments at once makes every
    // worker miss its startup handshake and the whole run reports `no tests` -
    // which looks like a broken suite rather than an exhausted machine. Four is
    // green and no slower, and CI runners have four cores anyway.
    maxWorkers: 4,
    // A hang detector, not a measurement - the same reasoning as the scenario
    // timeouts in apps/api. Vitest's 5 s default is below what a render plus a
    // userEvent.type of a long password costs on this downclocked CPU: the
    // register page's happy path measured 2.8 s passing, so it failed at the
    // default reproducibly and alone, which reads as a broken page rather than
    // an exhausted machine. Tightening this back only turns load into a red
    // suite; a test that really hangs still fails, four seconds later.
    testTimeout: 20_000,
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
    // Playwright owns e2e/; Vitest must not try to run those specs.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'html', 'lcov'],
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.{test,spec}.{ts,tsx}', 'src/app/**/layout.tsx', 'src/**/*.d.ts'],
    },
  },
});
