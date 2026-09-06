import coreWebVitals from 'eslint-config-next/core-web-vitals';
import nextTypescript from 'eslint-config-next/typescript';

/**
 * Flat config. `eslint-config-next` v16 ships native flat configs, so no
 * FlatCompat shim is needed.
 */
const config = [
  {
    ignores: [
      '.next/**',
      'node_modules/**',
      'coverage/**',
      'playwright-report/**',
      'test-results/**',
      'next-env.d.ts',
    ],
  },
  ...coreWebVitals,
  ...nextTypescript,
  {
    rules: {
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
  {
    // Code that can reach the browser bundle. Route handlers, Playwright and
    // build config are server-side and are deliberately excluded.
    files: ['src/components/**/*.{ts,tsx}', 'src/lib/**/*.{ts,tsx}', 'src/app/**/*.tsx'],
    rules: {
      // Secrets must never reach the browser. Anything the client needs is
      // prefixed NEXT_PUBLIC_ and read through src/lib/api/config.ts.
      'no-restricted-syntax': [
        'error',
        {
          selector:
            "MemberExpression[object.object.name='process'][object.property.name='env'][property.name!=/^NEXT_PUBLIC_/][property.name!='NODE_ENV']",
          message:
            'Client code may only read NEXT_PUBLIC_* env vars. Server-only values belong in a route handler or server component.',
        },
      ],
    },
  },
];

export default config;
