'use client';

import type {
  CreateResearchRequest,
  ResearchRun,
  SystemMetrics,
  UserSettings,
} from '@aether/shared-types';
import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from '@tanstack/react-query';
import { ApiError } from './client';
import {
  authApi,
  evaluationsApi,
  researchApi,
  settingsApi,
  type ListRunsParams,
} from './endpoints';
import { queryKeys } from './query-keys';

/**
 * TanStack Query hooks - the only server state in the app.
 *
 * There is no global store. A component that needs run data asks for it here
 * and gets caching, deduplication, retry and background refresh for free. The
 * SSE hook writes into this same cache, so the live feed and the REST snapshot
 * cannot drift apart.
 */

/** Statuses after which polling should stop: the run will not change again. */
const TERMINAL: ReadonlySet<string> = new Set(['completed', 'failed', 'cancelled']);

export function isRunActive(run: Pick<ResearchRun, 'status'> | undefined): boolean {
  return run !== undefined && !TERMINAL.has(run.status);
}

/** Never retry a 4xx: a missing run will still be missing on attempt three. */
export function defaultRetry(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && !error.isRetryable) return false;
  return failureCount < 2;
}

export function useCurrentUser() {
  return useQuery({
    queryKey: queryKeys.auth.me(),
    queryFn: ({ signal }) => authApi.me(signal),
    retry: false,
    staleTime: 5 * 60_000,
  });
}

export function useSessions() {
  return useQuery({
    queryKey: queryKeys.auth.sessions(),
    queryFn: () => authApi.sessions(),
    retry: defaultRetry,
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: authApi.login,
    onSuccess: (data) => {
      queryClient.setQueryData(queryKeys.auth.me(), data.user);
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: authApi.logout,
    onSuccess: () => queryClient.clear(),
  });
}

export function useResearchList(params: ListRunsParams = {}) {
  return useQuery({
    queryKey: queryKeys.research.list(params),
    queryFn: ({ signal }) => researchApi.list(params, signal),
    retry: defaultRetry,
    // A dashboard left open should notice a run finishing without a reload.
    refetchInterval: 15_000,
  });
}

export function useDashboardStats() {
  return useQuery({
    queryKey: queryKeys.research.stats(),
    queryFn: ({ signal }) => researchApi.stats(signal),
    retry: defaultRetry,
    refetchInterval: 30_000,
  });
}

export function useResearchRun(id: string, options?: { enabled?: boolean }) {
  return useQuery({
    queryKey: queryKeys.research.detail(id),
    queryFn: ({ signal }) => researchApi.get(id, signal),
    enabled: options?.enabled ?? Boolean(id),
    retry: defaultRetry,
    // The SSE stream is the primary signal; this is the safety net for a
    // dropped stream, and it stops once the run reaches a terminal status.
    refetchInterval: (query) => (isRunActive(query.state.data) ? 10_000 : false),
  });
}

export function useResearchPlan(id: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.research.plan(id),
    queryFn: ({ signal }) => researchApi.plan(id, signal),
    enabled: enabled && Boolean(id),
    retry: defaultRetry,
  });
}

export function useResearchSources(id: string, type?: string) {
  return useQuery({
    queryKey: queryKeys.research.sources(id, type),
    queryFn: ({ signal }) => researchApi.sources(id, { type }, signal),
    enabled: Boolean(id),
    retry: defaultRetry,
  });
}

export function useResearchEvidence(id: string, status?: string) {
  return useQuery({
    queryKey: queryKeys.research.evidence(id, status),
    queryFn: ({ signal }) => researchApi.evidence(id, { status }, signal),
    enabled: Boolean(id),
    retry: defaultRetry,
  });
}

export function useResearchActivity(id: string) {
  return useQuery({
    queryKey: queryKeys.research.activity(id),
    queryFn: ({ signal }) => researchApi.activity(id, signal),
    enabled: Boolean(id),
    retry: defaultRetry,
  });
}

/**
 * The report only exists once synthesis and citation validation have finished,
 * so a 404 here is an expected state rather than an error to retry.
 */
export function useResearchReport(id: string, enabled = true) {
  return useQuery({
    queryKey: queryKeys.research.report(id),
    queryFn: ({ signal }) => researchApi.report(id, signal),
    enabled: enabled && Boolean(id),
    retry: (count, error) => !(error instanceof ApiError && error.isNotFound) && count < 2,
  });
}

export function useCreateResearch() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateResearchRequest) => researchApi.create(body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.research.all() });
    },
  });
}

export function useCancelResearch(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => researchApi.cancel(id),
    onSuccess: (run) => {
      queryClient.setQueryData(queryKeys.research.detail(id), run);
      void queryClient.invalidateQueries({ queryKey: queryKeys.research.all() });
    },
  });
}

export function useEvaluations(
  options?: Partial<UseQueryOptions<Awaited<ReturnType<typeof evaluationsApi.list>>>>,
) {
  return useQuery({
    queryKey: queryKeys.evaluations.all(),
    queryFn: ({ signal }) => evaluationsApi.list(signal),
    retry: defaultRetry,
    ...options,
  });
}

export function useSystemMetrics(window: SystemMetrics['window'] = '24h') {
  return useQuery({
    queryKey: queryKeys.evaluations.system(window),
    queryFn: ({ signal }) => evaluationsApi.systemMetrics(window, signal),
    retry: defaultRetry,
    refetchInterval: 30_000,
  });
}

export function useSettings() {
  return useQuery({
    queryKey: queryKeys.settings(),
    queryFn: ({ signal }) => settingsApi.get(signal),
    retry: defaultRetry,
  });
}

export function useUpdateSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<UserSettings>) => settingsApi.update(body),
    onSuccess: (settings) => queryClient.setQueryData(queryKeys.settings(), settings),
  });
}
