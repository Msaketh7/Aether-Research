/** Conventional Commits, enforced on every commit. */
export default {
  extends: ['@commitlint/config-conventional'],
  rules: {
    'scope-enum': [
      2,
      'always',
      [
        'repo',
        'docs',
        'web',
        'api',
        'worker',
        'agents',
        'retrieval',
        'db',
        'infra',
        'ci',
        'eval',
        'types',
        'deps',
      ],
    ],
    'header-max-length': [2, 'always', 100],
  },
};
