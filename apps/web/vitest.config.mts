import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  // Resolves the `@/*` alias from tsconfig.json natively.
  resolve: { tsconfigPaths: true },
  test: {
    environment: 'jsdom',
    // The forks pool fails to hand off to workers when the repository path
    // contains a space (Windows); threads is unaffected and is faster here.
    pool: 'threads',
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
