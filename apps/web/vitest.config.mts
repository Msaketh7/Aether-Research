import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  // Resolves the `@/*` alias from tsconfig.json natively.
  resolve: { tsconfigPaths: true },
  test: {
    environment: 'jsdom',
    // Threads rather than forks: measurably faster here, and forks additionally
    // failed to hand off to workers while the repository lived under a path
    // containing a space (Windows). It no longer does, but threads stays.
    pool: 'threads',
    // Vitest defaults to one worker per core. On a laptop CPU that is being
    // downclocked, spinning up that many jsdom environments at once makes every
    // worker miss its startup handshake and the whole run reports `no tests` -
    // which looks like a broken suite rather than an exhausted machine. Four is
    // green and no slower, and CI runners have four cores anyway.
    maxWorkers: 4,
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
