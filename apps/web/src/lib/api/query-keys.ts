import type { ListRunsParams } from './endpoints';

/**
 * Query key factory.
 *
 * Every key is derived here so invalidation is precise: the SSE stream
 * invalidates exactly the run it is watching, never the whole cache.
 */
export const queryKeys = {
  auth: {
    me: () => ['auth', 'me'] as const,
    sessions: () => ['auth', 'sessions'] as const,
    ssoOptions: () => ['auth', 'sso-options'] as const,
    identities: () => ['auth', 'identities'] as const,
  },
  research: {
    all: () => ['research'] as const,
    list: (params: ListRunsParams) => ['research', 'list', params] as const,
    stats: () => ['research', 'stats'] as const,
    detail: (id: string) => ['research', 'detail', id] as const,
    plan: (id: string) => ['research', 'plan', id] as const,
    sources: (id: string, filter?: string) => ['research', 'sources', id, filter ?? 'all'] as const,
    evidence: (id: string, filter?: string) =>
      ['research', 'evidence', id, filter ?? 'all'] as const,
    activity: (id: string) => ['research', 'activity', id] as const,
    report: (id: string) => ['research', 'report', id] as const,
    answer: (id: string) => ['research', 'answer', id] as const,
  },
  evaluations: {
    all: () => ['evaluations'] as const,
    system: (window: string) => ['evaluations', 'system', window] as const,
  },
  settings: () => ['settings'] as const,
} as const;
