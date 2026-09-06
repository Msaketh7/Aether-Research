import type {
  EvaluationCaseResult,
  EvaluationRun,
  EvaluationsResponse,
  MetricValue,
  SystemMetrics,
} from '@aether/shared-types';
import { stableUuid } from './rng';

/**
 * Evaluation and telemetry fixtures.
 *
 * IMPORTANT: no benchmark has been executed - the evaluation suite is built in
 * Phase 18. These values exist so the dashboard can be designed and tested, and
 * they are self-labelling: `dataset_version` and `git_sha` both say `fixture`,
 * and the evaluations page renders an explicit notice whenever the API is in
 * mock mode. See docs/evaluation.md for the rule this follows.
 */

function metric(
  key: string,
  label: string,
  value: number | null,
  unit: MetricValue['unit'],
  threshold: number | null,
  delta: number | null = null,
): MetricValue {
  return {
    key,
    label,
    value,
    unit,
    threshold,
    passed: value === null || threshold === null ? null : value >= threshold,
    delta,
  };
}

const RETRIEVAL_METRICS: MetricValue[] = [
  metric('recall_at_10', 'Recall@10', 0.82, 'ratio', 0.75, 0.03),
  metric('precision_at_10', 'Precision@10', 0.64, 'ratio', null, -0.01),
  metric('mrr', 'MRR', 0.71, 'ratio', null, 0.02),
  metric('ndcg', 'NDCG@10', 0.77, 'ratio', null, 0.01),
];

const GENERATION_METRICS: MetricValue[] = [
  metric('groundedness', 'Groundedness', 0.91, 'ratio', 0.9, 0.02),
  metric('citation_precision', 'Citation precision', 0.95, 'ratio', 0.95, 0.0),
  metric('citation_recall', 'Citation recall', 0.88, 'ratio', 0.85, 0.04),
  metric('faithfulness', 'Faithfulness', 0.89, 'ratio', 0.85, -0.01),
  metric('correctness', 'Correctness', 0.83, 'ratio', 0.8, 0.03),
];

const AGENT_METRICS: MetricValue[] = [
  metric('task_completion', 'Task completion', 0.94, 'ratio', 0.9, 0.01),
  metric('tool_selection', 'Tool selection accuracy', 0.87, 'ratio', null, 0.02),
  metric('unnecessary_tool_calls', 'Unnecessary tool calls', 6, 'count', null, -2),
  metric('planning_quality', 'Planning quality', 0.85, 'ratio', null, 0.0),
  metric('recovery_rate', 'Recovery rate', 0.8, 'ratio', null, 0.05),
];

const SYSTEM_EVAL_METRICS: MetricValue[] = [
  metric('latency_p95', 'Latency P95', 148, 'seconds', null, -12),
  metric('cost_per_run', 'Cost per run', 1.34, 'usd', null, -0.08),
  metric('failure_rate', 'Failure rate', 0.04, 'ratio', null, -0.01),
];

const ALL_METRICS = [
  ...RETRIEVAL_METRICS,
  ...GENERATION_METRICS,
  ...AGENT_METRICS,
  ...SYSTEM_EVAL_METRICS,
];

function evaluationRun(index: number, daysAgo: number, passed: boolean): EvaluationRun {
  const started = new Date(Date.now() - daysAgo * 86_400_000);
  // Slightly degrade older runs so the history chart has a shape.
  const drift = index * 0.012;
  return {
    id: stableUuid(`evaluation:${index}`),
    dataset_version: 'fixture',
    git_sha: 'fixture',
    kind: 'full',
    model_config: {
      planner: 'claude-haiku-4-5',
      researcher: 'claude-sonnet-4-5',
      critic: 'claude-sonnet-4-5',
      synthesizer: 'claude-opus-4-6',
      judge: 'claude-sonnet-4-5',
    },
    started_at: started.toISOString(),
    completed_at: new Date(started.getTime() + 21 * 60_000).toISOString(),
    passed,
    case_count: 12,
    passed_count: passed ? 11 : 9,
    metrics: ALL_METRICS.map((m) =>
      m.value === null || m.unit !== 'ratio'
        ? m
        : { ...m, value: Number(Math.max(0, m.value - drift).toFixed(3)) },
    ),
  };
}

const CASES: EvaluationCaseResult[] = [
  {
    case_id: 'inference-infra-comparison',
    question: 'Compare the major AI inference infrastructure companies.',
    kind: 'full',
    passed: true,
    metrics: [GENERATION_METRICS[0]!, GENERATION_METRICS[1]!, RETRIEVAL_METRICS[0]!],
    failures: [],
    run_id: null,
    duration_seconds: 164,
  },
  {
    case_id: 'contradictory-revenue-figures',
    question: 'What revenue did the company report for the most recent fiscal period?',
    kind: 'generation',
    passed: true,
    metrics: [GENERATION_METRICS[1]!, GENERATION_METRICS[3]!],
    failures: [],
    run_id: null,
    duration_seconds: 88,
  },
  {
    case_id: 'no-good-sources',
    question: 'What is the internal roadmap of a private company with no public disclosure?',
    kind: 'agent',
    passed: true,
    metrics: [AGENT_METRICS[0]!, AGENT_METRICS[4]!],
    failures: [],
    run_id: null,
    duration_seconds: 52,
  },
  {
    case_id: 'multi-entity-comparison',
    question: 'Compare four providers across six dimensions with citations for each cell.',
    kind: 'full',
    passed: false,
    metrics: [GENERATION_METRICS[2]!, RETRIEVAL_METRICS[0]!],
    failures: ['Citation recall 0.79 is below the 0.85 gate for 2 of 6 dimensions.'],
    run_id: null,
    duration_seconds: 201,
  },
];

export const EVALUATIONS_FIXTURE: EvaluationsResponse = {
  latest: evaluationRun(0, 1, true),
  history: [
    evaluationRun(0, 1, true),
    evaluationRun(1, 4, true),
    evaluationRun(2, 8, false),
    evaluationRun(3, 12, true),
    evaluationRun(4, 18, true),
  ],
  cases: CASES,
};

export const SYSTEM_METRICS_FIXTURE: SystemMetrics = {
  window: '24h',
  research_success_rate: 0.94,
  research_failure_rate: 0.06,
  latency_p50_seconds: 96,
  latency_p95_seconds: 148,
  latency_p99_seconds: 212,
  llm_latency_p95_ms: 4_180,
  tool_latency_p95_ms: 1_240,
  cache_hit_rate: 0.38,
  total_tokens: 1_842_000,
  total_cost_usd: 18.42,
  queue_depth: 2,
  active_workers: 3,
};
