import type {
  ActivityResponse,
  AnswerResponse,
  CreateResearchRequest,
  DashboardStats,
  EvaluationsResponse,
  EvidenceResponse,
  ReportResponse,
  ResearchMode,
  ResearchPlan,
  ResearchRun,
  ResearchRunSummary,
  RunStatus,
  SourcesResponse,
  SystemMetrics,
  User,
  UserSettings,
} from '@aether/shared-types';
import { HISTORY_QUESTIONS } from './corpus';
import {
  DEMO_USER_ID,
  buildDataset,
  runDurationMs,
  type RunDataset,
  type RunSpec,
} from './fixtures';
import { EVALUATIONS_FIXTURE, SYSTEM_METRICS_FIXTURE } from './metrics';
import { stableUuid } from './rng';
import { buildTimeline, statusAt, type TimelineEntry } from './timeline';

/**
 * In-memory mock backend (ADR 0009).
 *
 * Run progress is *derived from elapsed time* rather than driven by a timer:
 * every endpoint computes what the run looked like at `now`, so the REST
 * snapshot and the SSE stream cannot disagree, and a page refresh mid-run shows
 * exactly the state the stream would have produced.
 *
 * Deleted in Phase 2, when the FastAPI service takes over these routes.
 */

export interface RunEntry {
  dataset: RunDataset;
  timeline: TimelineEntry[];
  /** Epoch ms when the simulated run began. */
  startedAtMs: number;
  /** Set by POST /research/{id}/cancel. */
  cancelledAtMs: number | null;
  /** Seeded runs are already finished and never re-simulate. */
  frozen: boolean;
}

interface Store {
  runs: Map<string, RunEntry>;
  order: string[];
  settings: UserSettings;
}

const DAY_MS = 86_400_000;

export const DEMO_USER: User = {
  id: DEMO_USER_ID,
  email: 'analyst@aether.dev',
  name: 'Demo Analyst',
  role: 'user',
  created_at: new Date(Date.now() - 90 * DAY_MS).toISOString(),
  last_login_at: new Date().toISOString(),
};

const FLAGSHIP_QUESTION =
  'Compare the major AI inference infrastructure companies. Analyze their products, technology, pricing, funding, financial performance, recent announcements, risks, competitive advantages, and market opportunities.';

function makeSpec(
  overrides: Partial<RunSpec> & Pick<RunSpec, 'id' | 'title' | 'question'>,
): RunSpec {
  return {
    mode: 'deep',
    depth: 3,
    domains: [],
    dateRangeStart: null,
    dateRangeEnd: null,
    createdAt: new Date(),
    startedAt: new Date(),
    status: 'completed',
    coverage: 1,
    coverageCaveat: null,
    parentRunId: null,
    ...overrides,
  };
}

function register(store: Store, dataset: RunDataset, frozen: boolean): RunEntry {
  const timeline = buildTimeline(dataset);
  const entry: RunEntry = {
    dataset,
    timeline,
    startedAtMs: (dataset.spec.startedAt ?? dataset.spec.createdAt).getTime(),
    cancelledAtMs: null,
    frozen,
  };
  store.runs.set(dataset.spec.id, entry);
  store.order.unshift(dataset.spec.id);
  return entry;
}

function seed(): Store {
  const store: Store = {
    runs: new Map(),
    order: [],
    settings: {
      preferred_provider: null,
      default_mode: 'deep',
      default_depth: 3,
      notify_on_completion: true,
    },
  };

  const now = Date.now();

  // Historical runs, oldest first so `order` ends up newest-first.
  [...HISTORY_QUESTIONS]
    .sort((a, b) => b.days_ago - a.days_ago)
    .forEach((seedRun, index) => {
      const createdAt = new Date(now - seedRun.days_ago * DAY_MS);
      const dataset = buildDataset(
        makeSpec({
          id: stableUuid(`run:history:${index}`),
          title: seedRun.title,
          question: seedRun.question,
          mode: seedRun.mode,
          depth: seedRun.mode === 'quick' ? 1 : 3,
          createdAt,
          startedAt: new Date(createdAt.getTime() + 1_500),
          status: seedRun.status,
          coverage: seedRun.mode === 'quick' ? 0.35 : 0.7,
          coverageCaveat:
            seedRun.status === 'completed' && seedRun.mode === 'quick'
              ? 'Quick mode: one retrieval round, no critic loop.'
              : null,
        }),
        false,
      );
      register(store, dataset, true);
    });

  // The flagship completed run: the reference dataset for the whole UI.
  const flagshipCreated = new Date(now - 26 * 60_000);
  register(
    store,
    buildDataset(
      makeSpec({
        id: stableUuid('run:flagship'),
        title: 'AI inference infrastructure landscape',
        question: FLAGSHIP_QUESTION,
        mode: 'deep',
        depth: 4,
        domains: ['sec.gov', 'arxiv.org', 'github.com'],
        dateRangeStart: new Date(now - 365 * DAY_MS).toISOString().slice(0, 10),
        dateRangeEnd: new Date(now).toISOString().slice(0, 10),
        createdAt: flagshipCreated,
        startedAt: new Date(flagshipCreated.getTime() + 900),
        status: 'completed',
        coverage: 1,
      }),
      true,
    ),
    true,
  );

  // One run in flight from process start, so the dashboard is never static.
  const liveCreated = new Date(now - 8_000);
  register(
    store,
    buildDataset(
      makeSpec({
        id: stableUuid('run:live'),
        title: 'Serving-stack benchmark practices',
        question:
          'How should inference serving stacks be benchmarked so that vendor throughput claims become comparable?',
        mode: 'deep',
        depth: 3,
        createdAt: liveCreated,
        startedAt: liveCreated,
        status: 'completed',
        coverage: 0.85,
      }),
      false,
    ),
    false,
  );

  return store;
}

// Survives Next.js hot reloads in development, which re-evaluate modules.
const globalForStore = globalThis as unknown as { __aetherMockStore?: Store };
const store: Store = globalForStore.__aetherMockStore ?? seed();
globalForStore.__aetherMockStore = store;

// --------------------------------------------------------------------------
// Time-derived projection
// --------------------------------------------------------------------------

/** Speed multiplier so Playwright does not wait 96 s for a run. */
function speed(): number {
  const raw = Number(process.env.NEXT_PUBLIC_MOCK_SPEED ?? '1');
  return Number.isFinite(raw) && raw >= 1 ? raw : 1;
}

export function elapsedMsFor(entry: RunEntry, now = Date.now()): number {
  if (entry.frozen) return entry.dataset.durationMs;
  const end = entry.cancelledAtMs ?? now;
  return Math.max(0, Math.min((end - entry.startedAtMs) * speed(), entry.dataset.durationMs));
}

function statusFor(entry: RunEntry, elapsed: number): RunStatus {
  if (entry.frozen) return entry.dataset.spec.status;
  if (entry.cancelledAtMs !== null) return 'cancelled';
  return statusAt(entry.timeline, elapsed, 'queued');
}

function progressFor(entry: RunEntry, elapsed: number, status: RunStatus): number {
  if (status === 'completed') return 1;
  if (status === 'cancelled' || status === 'failed') {
    return Number(Math.min(1, elapsed / entry.dataset.durationMs).toFixed(2));
  }
  return Number(Math.min(0.99, elapsed / entry.dataset.durationMs).toFixed(2));
}

/** The run as it looked at `now`. */
export function projectRun(entry: RunEntry, now = Date.now()): ResearchRun {
  const elapsed = elapsedMsFor(entry, now);
  const status = statusFor(entry, elapsed);
  const base = entry.dataset.run;
  const ratio = elapsed / entry.dataset.durationMs;

  const sources = visibleSources(entry, now).length;
  const claims = visibleClaims(entry, now).length;
  const contradictions = visibleContradictions(entry, now).length;
  const done = status === 'completed' || status === 'failed' || status === 'cancelled';

  return {
    ...base,
    status,
    progress: progressFor(entry, elapsed, status),
    source_count: sources,
    claim_count: claims,
    contradiction_count: contradictions,
    has_report: status === 'completed' && entry.dataset.report !== null,
    usage: {
      ...base.usage,
      sources,
      iterations: elapsed > entry.dataset.durationMs * 0.73 ? base.usage.iterations : 1,
      elapsed_seconds: Math.round(elapsed / 1000),
      total_tokens: Math.round(base.usage.total_tokens * Math.min(1, ratio)),
      cost_usd: Number((base.usage.cost_usd * Math.min(1, ratio)).toFixed(4)),
    },
    started_at: entry.dataset.spec.startedAt ? entry.dataset.spec.startedAt.toISOString() : null,
    completed_at: done
      ? new Date(entry.startedAtMs + entry.dataset.durationMs).toISOString()
      : null,
    error: status === 'failed' ? base.error : null,
  };
}

function offsetIndex(entry: RunEntry, type: string, key: string): Map<string, number> {
  const map = new Map<string, number>();
  for (const item of entry.timeline) {
    if (item.event.type !== type) continue;
    const payload = item.event.payload as Record<string, unknown>;
    const id = payload[key];
    if (typeof id === 'string' && !map.has(id)) map.set(id, item.offsetMs);
  }
  return map;
}

export function visibleSources(entry: RunEntry, now = Date.now()) {
  const elapsed = elapsedMsFor(entry, now);
  const offsets = offsetIndex(entry, 'source_found', 'source_id');
  return entry.dataset.sources.filter((source) => (offsets.get(source.id) ?? 0) <= elapsed);
}

export function visibleClaims(entry: RunEntry, now = Date.now()) {
  const elapsed = elapsedMsFor(entry, now);
  const offsets = offsetIndex(entry, 'claim_extracted', 'claim_id');
  return entry.dataset.claims.filter((claim) => (offsets.get(claim.id) ?? Infinity) <= elapsed);
}

export function visibleContradictions(entry: RunEntry, now = Date.now()) {
  const elapsed = elapsedMsFor(entry, now);
  const offsets = offsetIndex(entry, 'contradiction_found', 'contradiction_id');
  return entry.dataset.contradictions.filter(
    (item) => (offsets.get(item.id) ?? Infinity) <= elapsed,
  );
}

// --------------------------------------------------------------------------
// Public store API, one function per mock route
// --------------------------------------------------------------------------

export function getEntry(id: string): RunEntry | undefined {
  return store.runs.get(id);
}

export function toSummary(run: ResearchRun): ResearchRunSummary {
  return {
    id: run.id,
    title: run.title,
    question: run.question,
    mode: run.mode,
    status: run.status,
    progress: run.progress,
    source_count: run.source_count,
    claim_count: run.claim_count,
    contradiction_count: run.contradiction_count,
    cost_usd: run.usage.cost_usd,
    created_at: run.created_at,
    completed_at: run.completed_at,
  };
}

export interface ListOptions {
  limit?: number;
  cursor?: string | null;
  status?: string | null;
  mode?: string | null;
  q?: string | null;
}

export function listRuns(options: ListOptions = {}) {
  const { limit = 20, cursor, status, mode, q } = options;
  const now = Date.now();

  let runs = store.order
    .map((id) => store.runs.get(id))
    .filter((entry): entry is RunEntry => entry !== undefined)
    .map((entry) => projectRun(entry, now));

  if (status) runs = runs.filter((run) => run.status === status);
  if (mode) runs = runs.filter((run) => run.mode === mode);
  if (q) {
    const needle = q.toLowerCase();
    runs = runs.filter(
      (run) =>
        run.title.toLowerCase().includes(needle) || run.question.toLowerCase().includes(needle),
    );
  }

  runs.sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));

  const start = cursor ? Math.max(0, runs.findIndex((run) => run.id === cursor) + 1) : 0;
  const page = runs.slice(start, start + limit);
  const nextIndex = start + limit;

  return {
    items: page.map(toSummary),
    next_cursor: nextIndex < runs.length ? (page.at(-1)?.id ?? null) : null,
    total: runs.length,
  };
}

export function stats(): DashboardStats {
  const now = Date.now();
  const runs = store.order
    .map((id) => store.runs.get(id))
    .filter((entry): entry is RunEntry => entry !== undefined)
    .map((entry) => projectRun(entry, now));

  const completed = runs.filter((run) => run.status === 'completed');
  const durations = completed
    .filter((run) => run.completed_at && run.started_at)
    .map((run) => (Date.parse(run.completed_at!) - Date.parse(run.started_at!)) / 1000)
    .sort((a, b) => a - b);

  return {
    total_runs: runs.length,
    completed_runs: completed.length,
    running_runs: runs.filter((run) => !['completed', 'failed', 'cancelled'].includes(run.status))
      .length,
    failed_runs: runs.filter((run) => run.status === 'failed').length,
    total_sources: runs.reduce((sum, run) => sum + run.source_count, 0),
    total_claims: runs.reduce((sum, run) => sum + run.claim_count, 0),
    total_cost_usd: Number(runs.reduce((sum, run) => sum + run.usage.cost_usd, 0).toFixed(2)),
    median_runtime_seconds:
      durations.length >= 3 ? Math.round(durations[Math.floor(durations.length / 2)] ?? 0) : null,
  };
}

export function createRun(request: CreateResearchRequest): ResearchRun {
  const now = new Date();
  const id = stableUuid(`run:created:${now.getTime()}:${request.question.slice(0, 40)}`);
  const mode: ResearchMode = request.mode;

  const dataset = buildDataset(
    makeSpec({
      id,
      title: deriveTitle(request.question),
      question: request.question,
      mode,
      depth: request.depth ?? 3,
      domains: request.domains ?? [],
      dateRangeStart: request.date_range_start ?? null,
      dateRangeEnd: request.date_range_end ?? null,
      createdAt: now,
      startedAt: now,
      status: 'completed',
      coverage: mode === 'quick' ? 0.4 : 1,
      parentRunId: request.parent_run_id ?? null,
    }),
    true,
  );

  const entry = register(store, dataset, false);
  return projectRun(entry, now.getTime());
}

/** First clause of the question, capped - the same heuristic the API will use. */
function deriveTitle(question: string): string {
  const firstSentence = question.split(/[.?!]/)[0]?.trim() ?? question;
  const trimmed =
    firstSentence.length > 68 ? `${firstSentence.slice(0, 65).trimEnd()}…` : firstSentence;
  return trimmed || 'Untitled research';
}

export function cancelRun(id: string): ResearchRun | undefined {
  const entry = store.runs.get(id);
  if (!entry) return undefined;
  if (!entry.frozen && entry.cancelledAtMs === null) entry.cancelledAtMs = Date.now();
  return projectRun(entry);
}

export function getPlan(entry: RunEntry, now = Date.now()): ResearchPlan {
  const elapsed = elapsedMsFor(entry, now);
  const iterationTwoStart = entry.dataset.durationMs * 0.735;
  return {
    ...entry.dataset.plan,
    tasks: entry.dataset.plan.tasks.filter(
      (task) => task.iteration === 1 || elapsed >= iterationTwoStart,
    ),
    iteration: elapsed >= iterationTwoStart ? entry.dataset.plan.iteration : 1,
  };
}

export function getSources(
  entry: RunEntry,
  type?: string | null,
  now = Date.now(),
): SourcesResponse {
  let sources = visibleSources(entry, now);
  if (type) sources = sources.filter((source) => source.source_type === type);
  const visibleIds = new Set(sources.map((source) => source.id));
  return {
    sources,
    clusters: entry.dataset.clusters.filter((cluster) => visibleIds.has(cluster.primary_source_id)),
    next_cursor: null,
    total: sources.length,
  };
}

export function getEvidence(
  entry: RunEntry,
  status?: string | null,
  now = Date.now(),
): EvidenceResponse {
  let claims = visibleClaims(entry, now);
  if (status) claims = claims.filter((claim) => claim.status === status);
  return {
    claims,
    contradictions: visibleContradictions(entry, now),
    next_cursor: null,
    total: claims.length,
  };
}

export function getActivity(entry: RunEntry, now = Date.now()): ActivityResponse {
  const cutoff = entry.startedAtMs + elapsedMsFor(entry, now);
  const { agent_runs, tool_calls, llm_calls } = entry.dataset.activity;
  const visibleAgents = agent_runs.filter((record) => Date.parse(record.started_at) <= cutoff);
  const visibleAgentIds = new Set(visibleAgents.map((record) => record.id));
  return {
    agent_runs: visibleAgents,
    tool_calls: tool_calls.filter(
      (call) => visibleAgentIds.has(call.agent_run_id) && Date.parse(call.started_at) <= cutoff,
    ),
    llm_calls: llm_calls.filter(
      (call) => call.agent_run_id !== null && visibleAgentIds.has(call.agent_run_id),
    ),
  };
}

/**
 * The stored answer, once the run has written it.
 *
 * Gated on elapsed time like every other mock read, and on the same fraction
 * the timeline finishes streaming it at - so a page refreshed at 70% of a run
 * shows no answer, exactly as the stream had not yet delivered one, and the
 * REST snapshot and the stream cannot disagree.
 */
const ANSWER_READY_FRACTION = 0.825;

export function getAnswer(entry: RunEntry, now = Date.now()): AnswerResponse {
  const answer = entry.dataset.answer;
  if (!answer) return { answer: null };
  const elapsed = elapsedMsFor(entry, now);
  const status = statusFor(entry, elapsed);
  if (status === 'cancelled' || status === 'failed') return { answer: null };
  if (elapsed < entry.dataset.durationMs * ANSWER_READY_FRACTION) return { answer: null };
  return { answer };
}

export function getReport(entry: RunEntry, now = Date.now()): ReportResponse | undefined {
  const status = statusFor(entry, elapsedMsFor(entry, now));
  if (status !== 'completed') return undefined;
  return entry.dataset.report ?? undefined;
}

export function getSettings(): UserSettings {
  return store.settings;
}

export function updateSettings(patch: Partial<UserSettings>): UserSettings {
  store.settings = { ...store.settings, ...patch };
  return store.settings;
}

export function getEvaluations(): EvaluationsResponse {
  return EVALUATIONS_FIXTURE;
}

export function getSystemMetrics(window: SystemMetrics['window']): SystemMetrics {
  return { ...SYSTEM_METRICS_FIXTURE, window };
}

export { runDurationMs };
