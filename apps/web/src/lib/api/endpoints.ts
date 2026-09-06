import type {
  ActivityResponse,
  CreateResearchRequest,
  CreateResearchResponse,
  DashboardStats,
  EvaluationsResponse,
  EvidenceResponse,
  LoginRequest,
  LoginResponse,
  Page,
  ReportResponse,
  ResearchPlan,
  ResearchRun,
  ResearchRunSummary,
  SessionInfo,
  SourcesResponse,
  SystemMetrics,
  User,
  UserSettings,
} from '@aether/shared-types';
import { apiRequest } from './client';

/**
 * One function per endpoint in TDD section 18.
 *
 * These are transport-only: no caching, no React. The TanStack Query hooks in
 * `queries.ts` compose them. Keeping them separate means they are callable from
 * tests and from server components without a QueryClient.
 */

export const authApi = {
  login: (body: LoginRequest) => apiRequest<LoginResponse>('/auth/login', { method: 'POST', body }),
  logout: () => apiRequest<void>('/auth/logout', { method: 'POST' }),
  me: (signal?: AbortSignal) => apiRequest<User>('/auth/me', { signal }),
  sessions: () => apiRequest<SessionInfo[]>('/auth/sessions'),
  revokeSession: (id: string) => apiRequest<void>(`/auth/sessions/${id}`, { method: 'DELETE' }),
};

export interface ListRunsParams {
  cursor?: string;
  limit?: number;
  status?: string;
  mode?: string;
  q?: string;
}

export const researchApi = {
  list: (params: ListRunsParams = {}, signal?: AbortSignal) =>
    apiRequest<Page<ResearchRunSummary>>('/research', { query: { ...params }, signal }),

  stats: (signal?: AbortSignal) => apiRequest<DashboardStats>('/research/stats', { signal }),

  create: (body: CreateResearchRequest) =>
    apiRequest<CreateResearchResponse>('/research', { method: 'POST', body }),

  get: (id: string, signal?: AbortSignal) => apiRequest<ResearchRun>(`/research/${id}`, { signal }),

  plan: (id: string, signal?: AbortSignal) =>
    apiRequest<ResearchPlan>(`/research/${id}/plan`, { signal }),

  sources: (id: string, params: { cursor?: string; type?: string } = {}, signal?: AbortSignal) =>
    apiRequest<SourcesResponse>(`/research/${id}/sources`, { query: { ...params }, signal }),

  evidence: (id: string, params: { cursor?: string; status?: string } = {}, signal?: AbortSignal) =>
    apiRequest<EvidenceResponse>(`/research/${id}/evidence`, {
      query: { ...params },
      signal,
    }),

  activity: (id: string, signal?: AbortSignal) =>
    apiRequest<ActivityResponse>(`/research/${id}/activity`, { signal }),

  report: (id: string, signal?: AbortSignal) =>
    apiRequest<ReportResponse>(`/research/${id}/report`, { signal }),

  cancel: (id: string) => apiRequest<ResearchRun>(`/research/${id}/cancel`, { method: 'POST' }),

  followUp: (id: string, question: string) =>
    apiRequest<CreateResearchResponse>(`/research/${id}/followup`, {
      method: 'POST',
      body: { question },
    }),
};

export const evaluationsApi = {
  list: (signal?: AbortSignal) => apiRequest<EvaluationsResponse>('/evaluations', { signal }),
  systemMetrics: (window: SystemMetrics['window'] = '24h', signal?: AbortSignal) =>
    apiRequest<SystemMetrics>('/evaluations/system', { query: { window }, signal }),
};

export const settingsApi = {
  get: (signal?: AbortSignal) => apiRequest<UserSettings>('/settings', { signal }),
  update: (body: Partial<UserSettings>) =>
    apiRequest<UserSettings>('/settings', { method: 'PATCH', body }),
};
