import type {
  ActivityResponse,
  AgentRunRecord,
  Citation,
  RunAnswer,
  ClaimWithEvidence,
  Contradiction,
  Evidence,
  LlmCallRecord,
  Report,
  ReportResponse,
  ReportSection,
  ResearchMode,
  ResearchPlan,
  ResearchRun,
  ResearchTask,
  RunStatus,
  Source,
  SourceCluster,
  ToolCallRecord,
} from '@aether/shared-types';
import {
  CLAIMS,
  CONTRADICTIONS,
  DUPLICATE_PAIR,
  REPORT_SECTIONS,
  SOURCES,
  SUBTASKS,
  type SectionSeed,
} from './corpus';
import { between, createRng, hashString, intBetween, stableHash, stableUuid } from './rng';

/**
 * Builds a complete, self-consistent research dataset from the fixture corpus.
 *
 * "Self-consistent" is the point: every citation resolves to a claim, every
 * claim resolves to evidence, and every evidence span resolves to a source that
 * appears in the source list. The mock API therefore exercises the same
 * traceability the real citation validator will enforce, so the UI is built
 * against valid data rather than against convenient data.
 */

export interface RunSpec {
  id: string;
  title: string;
  question: string;
  mode: ResearchMode;
  depth: number;
  domains: string[];
  dateRangeStart: string | null;
  dateRangeEnd: string | null;
  createdAt: Date;
  startedAt: Date | null;
  /** Terminal status for seeded runs; live runs recompute it from elapsed time. */
  status: RunStatus;
  /** Fraction of the corpus this run covers. 1 = the full flagship dataset. */
  coverage: number;
  /** Set when a hard limit truncated the run. */
  coverageCaveat: string | null;
  parentRunId: string | null;
}

export interface RunDataset {
  spec: RunSpec;
  run: ResearchRun;
  plan: ResearchPlan;
  sources: Source[];
  clusters: SourceCluster[];
  claims: ClaimWithEvidence[];
  contradictions: Contradiction[];
  report: ReportResponse | null;
  /** The direct answer, written before the report. `null` when the run never
   *  got far enough to write one - cancelled, or failed during discovery. */
  answer: RunAnswer | null;
  activity: ActivityResponse;
  /** Wall-clock milliseconds the simulated run takes end to end. */
  durationMs: number;
}

const DAY_MS = 86_400_000;

/** The single demo user every fixture run belongs to. */
export const DEMO_USER_ID = stableUuid('user:demo');

function iso(date: Date): string {
  return date.toISOString();
}

function shift(base: Date, ms: number): Date {
  return new Date(base.getTime() + ms);
}

/** Deep runs take longer than quick runs; both stay demo-friendly. */
export function runDurationMs(mode: ResearchMode): number {
  return mode === 'quick' ? 22_000 : 96_000;
}

/** The FR-8 ceilings. Quick runs get tighter ones because they do one pass. */
function limitsFor(mode: ResearchMode) {
  return mode === 'quick'
    ? {
        max_iterations: 1,
        max_sources: 12,
        max_search_queries: 30,
        max_runtime_seconds: 60,
        max_cost_usd: 0.5,
      }
    : {
        max_iterations: 4,
        max_sources: 50,
        max_search_queries: 30,
        max_runtime_seconds: 300,
        max_cost_usd: 2,
      };
}

// --------------------------------------------------------------------------
// Sources
// --------------------------------------------------------------------------

function buildSources(spec: RunSpec, taskIds: Set<string>): Source[] {
  const anchor = spec.startedAt ?? spec.createdAt;
  const count = Math.max(3, Math.round(SOURCES.length * spec.coverage));

  return SOURCES.slice(0, count).map((seed, index) => {
    const rng = createRng(hashString(`${spec.id}:source:${index}`));
    const id = stableUuid(`${spec.id}:source:${index}`);
    const isDuplicate = index === DUPLICATE_PAIR.duplicate;
    const clusterId =
      index === DUPLICATE_PAIR.primary || isDuplicate
        ? stableUuid(`${spec.id}:cluster:dupe`)
        : null;

    return {
      id,
      run_id: spec.id,
      url: seed.url,
      canonical_url: seed.url.split('?')[0] ?? seed.url,
      domain: seed.url.startsWith('upload://')
        ? 'uploaded-document'
        : new URL(seed.url).hostname.replace(/^www\./, ''),
      source_type: seed.source_type,
      title: seed.title,
      publisher: seed.publisher,
      author: seed.author ?? null,
      published_at: iso(shift(anchor, -seed.published_days_ago * DAY_MS)),
      accessed_at: iso(shift(anchor, index * 1_500)),
      content_hash: isDuplicate
        ? stableHash(`${spec.id}:source:${DUPLICATE_PAIR.primary}`)
        : stableHash(`${spec.id}:source:${index}`),
      credibility_score: between(rng, seed.tier === 'official' ? 0.78 : 0.5, 0.97),
      credibility_metadata: {
        is_primary: seed.is_primary,
        tier: seed.tier,
        domain_reputation: between(rng, 0.5, 0.98),
        notes: isDuplicate ? 'Syndicated copy of an earlier primary source' : null,
      },
      dedup_cluster_id: clusterId,
      relevance_score: between(rng, 0.52, 0.97),
      task_external_id: taskIds.has(seed.task) ? seed.task : null,
      claim_count: 0,
      excerpt: seed.excerpt,
    } satisfies Source;
  });
}

function buildClusters(spec: RunSpec, sources: Source[]): SourceCluster[] {
  const primary = sources[DUPLICATE_PAIR.primary];
  const duplicate = sources[DUPLICATE_PAIR.duplicate];
  if (!primary || !duplicate) return [];
  return [
    {
      cluster_id: stableUuid(`${spec.id}:cluster:dupe`),
      primary_source_id: primary.id,
      duplicate_source_ids: [duplicate.id],
      reason: 'exact_hash',
    },
  ];
}

// --------------------------------------------------------------------------
// Plan
// --------------------------------------------------------------------------

function buildTasks(spec: RunSpec): ResearchTask[] {
  const anchor = spec.startedAt ?? spec.createdAt;
  const maxIteration = spec.mode === 'quick' ? 1 : 2;
  const seeds = SUBTASKS.filter((task) => task.iteration <= maxIteration).slice(
    0,
    Math.max(2, Math.round(SUBTASKS.length * spec.coverage)),
  );

  return seeds.map((seed, index) => {
    const rng = createRng(hashString(`${spec.id}:task:${seed.external_id}`));
    const terminated = spec.status === 'failed' || spec.status === 'cancelled';
    const done = !terminated || index < 2;
    return {
      id: stableUuid(`${spec.id}:task:${seed.external_id}`),
      run_id: spec.id,
      external_id: seed.external_id,
      question: seed.question,
      priority: seed.priority,
      status: done ? 'done' : 'insufficient',
      rationale: seed.rationale,
      iteration: seed.iteration,
      source_count: intBetween(rng, 2, 6),
      claim_count: intBetween(rng, 1, 4),
      completed_at: done ? iso(shift(anchor, 20_000 + index * 6_000)) : null,
    } satisfies ResearchTask;
  });
}

// --------------------------------------------------------------------------
// Claims, evidence, contradictions
// --------------------------------------------------------------------------

function buildEvidence(
  spec: RunSpec,
  claimIndex: number,
  claimId: string,
  sourceIndices: number[],
  sources: Source[],
  stance: 'supports' | 'refutes',
  span: string,
): Evidence[] {
  const anchor = spec.startedAt ?? spec.createdAt;
  return sourceIndices
    .map((sourceIndex, n): Evidence | null => {
      const source = sources[sourceIndex];
      if (!source) return null;
      const rng = createRng(hashString(`${spec.id}:ev:${claimIndex}:${sourceIndex}:${stance}`));
      const start = intBetween(rng, 200, 4_000);
      return {
        id: stableUuid(`${spec.id}:evidence:${claimIndex}:${sourceIndex}:${stance}`),
        claim_id: claimId,
        source_id: source.id,
        document_id: stableUuid(`${spec.id}:document:${sourceIndex}`),
        span_text: n === 0 ? span : `${span.slice(0, Math.max(24, span.length - 18))}…`,
        span_start: start,
        span_end: start + span.length,
        stance,
        extractor_agent: 'evidence_extractor',
        extractor_model: 'claude-sonnet-4-5',
        confidence: between(rng, 0.6, 0.96),
        created_at: iso(shift(anchor, 40_000 + claimIndex * 900)),
      };
    })
    .filter((item): item is Evidence => item !== null);
}

function buildClaims(spec: RunSpec, sources: Source[], taskIds: Set<string>): ClaimWithEvidence[] {
  const anchor = spec.startedAt ?? spec.createdAt;
  const count = Math.max(2, Math.round(CLAIMS.length * spec.coverage));

  return CLAIMS.slice(0, count)
    .filter((seed) => taskIds.has(seed.task))
    .map((seed, index) => {
      const id = stableUuid(`${spec.id}:claim:${index}`);
      const supporting = buildEvidence(
        spec,
        index,
        id,
        seed.supporting.filter((i) => i < sources.length),
        sources,
        'supports',
        seed.span,
      );
      const refuting = buildEvidence(
        spec,
        index,
        id,
        (seed.refuting ?? []).filter((i) => i < sources.length),
        sources,
        'refutes',
        seed.span,
      );

      return {
        id,
        run_id: spec.id,
        task_id: stableUuid(`${spec.id}:task:${seed.task}`),
        task_external_id: seed.task,
        text: seed.text,
        subject: seed.subject,
        predicate: seed.predicate,
        object_value: seed.object_value,
        claim_type: seed.claim_type,
        normalized_key: seed.normalized_key,
        confidence: seed.confidence,
        status: seed.status,
        corroboration_count: supporting.length,
        first_seen_at: iso(shift(anchor, 38_000 + index * 850)),
        supporting,
        refuting,
      } satisfies ClaimWithEvidence;
    });
}

function buildContradictions(
  spec: RunSpec,
  claims: ClaimWithEvidence[],
  sources: Source[],
): Contradiction[] {
  const anchor = spec.startedAt ?? spec.createdAt;
  return CONTRADICTIONS.map((seed, index) => {
    const claim = claims[seed.claim_a];
    const sourceA = sources[seed.source_a];
    const sourceB = sources[seed.source_b];
    if (!claim || !sourceA || !sourceB) return null;
    return {
      id: stableUuid(`${spec.id}:contradiction:${index}`),
      run_id: spec.id,
      normalized_key: seed.normalized_key,
      claim_a_id: claim.id,
      claim_b_id: claims[seed.claim_b]?.id ?? claim.id,
      claim_a_text: claim.text,
      claim_b_text: claims[seed.claim_b]?.text ?? claim.text,
      value_a: seed.value_a,
      value_b: seed.value_b,
      source_a_id: sourceA.id,
      source_b_id: sourceB.id,
      likely_reason: seed.likely_reason,
      resolution: seed.resolution,
      resolved_by: seed.resolution === 'unresolved' ? null : 'critic',
      detected_at: iso(shift(anchor, 62_000 + index * 2_400)),
    } satisfies Contradiction;
  }).filter((item): item is Contradiction => item !== null);
}

// --------------------------------------------------------------------------
// Report
// --------------------------------------------------------------------------

const CITATION_PATTERN = /\[c(\d+)\]/g;

function genericSections(question: string): SectionSeed[] {
  return [
    {
      kind: 'executive_summary',
      heading: 'Executive Summary',
      content_md: `This run examined: *${question}* The evidence base below supports the findings
that follow; claims that could not be corroborated by a second source are marked as candidates and
excluded from the key findings [c1][c2].`,
    },
    {
      kind: 'key_findings',
      heading: 'Key Findings',
      content_md: `1. The strongest supported finding rests on primary documentation [c1].
2. A second finding is supported but with narrower corroboration [c2].
3. Remaining material is recorded as evidence rather than as a finding [c3].`,
    },
    {
      kind: 'confidence_assessment',
      heading: 'Confidence Assessment',
      content_md: `Confidence is bounded by source diversity. Where a conclusion depends on a single
publisher it is reported as such rather than smoothed [c3].`,
    },
  ];
}

function buildReport(
  spec: RunSpec,
  claims: ClaimWithEvidence[],
  sources: Source[],
  flagship: boolean,
): ReportResponse | null {
  if (spec.status !== 'completed' || claims.length === 0) return null;

  const anchor = spec.startedAt ?? spec.createdAt;
  const reportId = stableUuid(`${spec.id}:report`);
  const seeds = flagship ? [...REPORT_SECTIONS] : genericSections(spec.question);

  // First pass: assign a stable ordinal to every claim the report references,
  // in the order it is first cited. This is what makes `[n]` meaningful.
  const ordinalByClaim = new Map<number, number>();
  for (const seed of seeds) {
    for (const match of seed.content_md.matchAll(CITATION_PATTERN)) {
      const claimIndex = Number(match[1]) - 1;
      if (!claims[claimIndex]) continue;
      if (!ordinalByClaim.has(claimIndex)) ordinalByClaim.set(claimIndex, ordinalByClaim.size + 1);
    }
  }

  const citations: Citation[] = [];
  const sections: ReportSection[] = seeds.map((seed, index) => {
    const sectionId = stableUuid(`${spec.id}:section:${seed.kind}`);
    const content = seed.content_md.replace(CITATION_PATTERN, (whole, digits: string) => {
      const claimIndex = Number(digits) - 1;
      const ordinal = ordinalByClaim.get(claimIndex);
      if (ordinal === undefined) return '';
      const claim = claims[claimIndex];
      const evidence = claim?.supporting[0];
      const source = sources.find((s) => s.id === evidence?.source_id);
      if (claim && evidence && source && !citations.some((c) => c.ordinal === ordinal)) {
        citations.push({
          id: stableUuid(`${spec.id}:citation:${ordinal}`),
          ordinal,
          report_section_id: sectionId,
          claim_id: claim.id,
          source_id: source.id,
          source_title: source.title,
          source_url: source.url,
          source_publisher: source.publisher,
          quote: evidence.span_text,
          confidence: claim.confidence,
        });
      }
      return ordinal === undefined ? whole : `[${ordinal}]`;
    });

    return {
      id: sectionId,
      report_id: reportId,
      kind: seed.kind,
      heading: seed.heading,
      ordinal: index + 1,
      content_md: content,
    } satisfies ReportSection;
  });

  // References are generated from the citation list, never authored, so the
  // reference section cannot contain a source the report did not actually cite.
  const referenceId = stableUuid(`${spec.id}:section:references`);
  sections.push({
    id: referenceId,
    report_id: reportId,
    kind: 'references',
    heading: 'References',
    ordinal: sections.length + 1,
    content_md: [...citations]
      .sort((a, b) => a.ordinal - b.ordinal)
      .map((c) => `${c.ordinal}. ${c.source_title} — ${c.source_publisher}. ${c.source_url}`)
      .join('\n'),
  });

  const wordCount = sections.reduce((total, s) => total + s.content_md.split(/\s+/).length, 0);
  const confidences = claims.map((c) => c.confidence);
  const overall =
    confidences.length === 0
      ? 0
      : Number((confidences.reduce((a, b) => a + b, 0) / confidences.length).toFixed(2));

  const report: Report = {
    id: reportId,
    run_id: spec.id,
    title: spec.title,
    summary: sections[0]?.content_md.slice(0, 320) ?? '',
    overall_confidence: overall,
    status: 'validated',
    model: 'claude-opus-4-6',
    word_count: wordCount,
    generated_at: iso(shift(anchor, runDurationMs(spec.mode) - 6_000)),
    validated_at: iso(shift(anchor, runDurationMs(spec.mode) - 1_000)),
    coverage_caveat: spec.coverageCaveat,
  };

  const rejected = flagship ? 2 : 0;
  return {
    report,
    sections,
    citations,
    validation: {
      checked: citations.length + rejected,
      valid: citations.length,
      rejected,
      rejection_reasons: rejected
        ? [{ reason: 'Evidence span not found in the stored document', count: rejected }]
        : [],
      validated_at: report.validated_at ?? report.generated_at,
    },
  };
}

// --------------------------------------------------------------------------
// Activity trace
// --------------------------------------------------------------------------

/**
 * The direct answer, built from the same claims the report cites.
 *
 * Its `[n]` markers are the *report's* ordinals, resolved through the citation
 * list the report endpoint returns - exactly as the real answerer's are. That
 * is what makes the mock exercise the real failure: a marker numbered against a
 * different catalogue would resolve to the wrong source while still looking
 * correct, and the renderer cannot tell the difference.
 *
 * `null` unless the run reached the answerer. A cancelled run is stopped before
 * it, and a failed one never got past discovery, so neither has an answer - the
 * same two cases the real graph produces.
 */
function buildAnswer(
  spec: RunSpec,
  claims: ClaimWithEvidence[],
  contradictions: Contradiction[],
  report: ReportResponse | null,
): RunAnswer | null {
  if (spec.status !== 'completed' || claims.length === 0 || report === null) return null;

  const cited = [...report.citations].sort((a, b) => a.ordinal - b.ordinal).slice(0, 4);
  if (cited.length === 0) return null;

  const claimById = new Map(claims.map((claim) => [claim.id, claim]));
  const sentence = (citation: Citation): string => {
    const text = claimById.get(citation.claim_id)?.text ?? citation.quote;
    const trimmed = text.replace(/\s+/g, ' ').trim().replace(/\.$/, '');
    return `${trimmed} [${citation.ordinal}].`;
  };

  const [lead, ...rest] = cited;
  const paragraphs: string[] = [
    // The answer, first sentence. Everything after it qualifies or bounds it.
    `${sentence(lead as Citation)} ${rest
      .slice(0, 2)
      .map((citation) => sentence(citation))
      .join(' ')}`.trim(),
  ];

  if (rest.length > 2) {
    paragraphs.push(
      rest
        .slice(2)
        .map((citation) => sentence(citation))
        .join(' '),
    );
  }

  if (contradictions.length > 0) {
    const first = contradictions[0];
    paragraphs.push(
      `Sources disagree on ${first?.normalized_key ?? 'one figure'}: ` +
        `${first?.value_a ?? 'one value'} against ${first?.value_b ?? 'another'}. ` +
        'Both are reported above rather than reconciled, because nothing in the ' +
        'retrieved material settles which is right.',
    );
  }

  paragraphs.push(
    `Drawn from ${claims.length} claims across ${report.citations.length} cited sources. ` +
      'The full report has the analysis, the evidence spans and the reference list.',
  );

  const content = paragraphs.join('\n\n');
  const anchor = spec.startedAt ?? spec.createdAt;
  return {
    id: stableUuid(`${spec.id}:answer`),
    run_id: spec.id,
    content_md: content,
    model: 'claude-opus-4-6',
    word_count: content.split(/\s+/).filter(Boolean).length,
    citation_count: cited.length,
    truncated: false,
    generated_at: iso(shift(anchor, runDurationMs(spec.mode) - 14_000)),
  };
}

function buildActivity(
  spec: RunSpec,
  plan: ResearchPlan,
  sources: Source[],
  claims: ClaimWithEvidence[],
): ActivityResponse {
  const anchor = spec.startedAt ?? spec.createdAt;
  const agentRuns: AgentRunRecord[] = [];
  const toolCalls: ToolCallRecord[] = [];
  const llmCalls: LlmCallRecord[] = [];

  const push = (
    key: string,
    agent: AgentRunRecord['agent_name'],
    offset: number,
    summary: string,
    options: { iteration?: number; status?: AgentRunRecord['status']; task?: string | null } = {},
  ): AgentRunRecord => {
    const rng = createRng(hashString(`${spec.id}:agent:${key}`));
    const latency = intBetween(rng, 900, 9_000);
    const tokens = intBetween(rng, 700, 9_000);
    const record: AgentRunRecord = {
      id: stableUuid(`${spec.id}:agent:${key}`),
      run_id: spec.id,
      task_external_id: options.task ?? null,
      agent_name: agent,
      iteration: options.iteration ?? 1,
      status: options.status ?? 'ok',
      summary,
      latency_ms: latency,
      tokens,
      cost_usd: Number((tokens * 0.000009).toFixed(4)),
      trace_id: stableHash(`${spec.id}:trace:${key}`).slice(0, 32),
      span_id: stableHash(`${spec.id}:span:${key}`).slice(0, 16),
      error: null,
      started_at: iso(shift(anchor, offset)),
      completed_at: iso(shift(anchor, offset + latency)),
    };
    agentRuns.push(record);

    llmCalls.push({
      id: stableUuid(`${spec.id}:llm:${key}`),
      agent_run_id: record.id,
      role: agent,
      provider: 'anthropic',
      model:
        agent === 'planner'
          ? 'claude-haiku-4-5'
          : agent === 'synthesizer'
            ? 'claude-opus-4-6'
            : 'claude-sonnet-4-5',
      prompt_version: `${agent}@1.2.0`,
      prompt_tokens: Math.round(tokens * 0.72),
      completion_tokens: Math.round(tokens * 0.28),
      total_tokens: tokens,
      cost_usd: record.cost_usd,
      latency_ms: latency,
      cache_hit: rng() > 0.78,
      status: 'ok',
      created_at: record.started_at,
    });

    return record;
  };

  const planner = push(
    'planner-1',
    'planner',
    1_200,
    `Decomposed into ${plan.tasks.length} subtasks`,
  );

  plan.tasks.forEach((task, index) => {
    const researcher = push(
      `researcher-${task.external_id}`,
      'researcher',
      8_000 + index * 2_200,
      `Searched and read sources for "${task.external_id}"`,
      { iteration: task.iteration, task: task.external_id },
    );
    const rng = createRng(hashString(`${spec.id}:tool:${task.external_id}`));
    toolCalls.push({
      id: stableUuid(`${spec.id}:tool:search:${task.external_id}`),
      agent_run_id: researcher.id,
      tool_name: 'search',
      request_summary: task.question.slice(0, 80),
      status: 'ok',
      latency_ms: intBetween(rng, 180, 1_400),
      cache_hit: rng() > 0.7,
      retries: 0,
      result_summary: `${intBetween(rng, 4, 12)} results`,
      started_at: researcher.started_at,
    });
  });

  sources.slice(0, 8).forEach((source, index) => {
    const owner = agentRuns.find((a) => a.task_external_id === source.task_external_id) ?? planner;
    const rng = createRng(hashString(`${spec.id}:fetch:${index}`));
    const failed = index === 6;
    toolCalls.push({
      id: stableUuid(`${spec.id}:tool:fetch:${index}`),
      agent_run_id: owner.id,
      tool_name: source.source_type === 'sec' ? 'sec_api' : 'fetch',
      request_summary: source.url,
      status: failed ? 'timeout' : 'ok',
      latency_ms: failed ? 10_000 : intBetween(rng, 220, 3_100),
      cache_hit: !failed && rng() > 0.75,
      retries: failed ? 2 : 0,
      result_summary: failed
        ? 'Read timeout after 10s, retried'
        : `${intBetween(rng, 3, 40)} KB extracted`,
      started_at: iso(shift(anchor, 12_000 + index * 1_800)),
    });
  });

  push('evidence-1', 'evidence_extractor', 38_000, `Extracted ${claims.length} candidate claims`);
  push('normalizer-1', 'claim_normalizer', 46_000, 'Normalized and deduplicated claims');
  push(
    'verifier-1',
    'verifier',
    54_000,
    `Corroborated ${claims.filter((c) => c.status === 'verified').length} claims`,
  );

  if (spec.mode !== 'quick') {
    push('critic-1', 'critic', 64_000, 'Coverage gap found: unit economics', { iteration: 1 });
    push('planner-2', 'planner', 68_000, 'Added 1 targeted subtask', { iteration: 2 });
  }

  if (spec.status === 'completed') {
    push('synthesizer-1', 'synthesizer', 78_000, 'Drafted report sections');
    push('validator-1', 'citation_validator', 90_000, 'Validated citations against evidence');
  }

  if (spec.status === 'failed') {
    const failing = push('researcher-failed', 'researcher', 30_000, 'Search provider unavailable', {
      status: 'error',
    });
    failing.error = {
      code: 'search_provider_unavailable',
      message: 'The search provider returned 503 on three consecutive attempts.',
    };
  }

  return { agent_runs: agentRuns, tool_calls: toolCalls, llm_calls: llmCalls };
}

// --------------------------------------------------------------------------
// Assembly
// --------------------------------------------------------------------------

export function buildDataset(spec: RunSpec, flagship: boolean): RunDataset {
  const tasks = buildTasks(spec);
  const taskIds = new Set(tasks.map((t) => t.external_id));
  const sources = buildSources(spec, taskIds);
  const clusters = buildClusters(spec, sources);
  const claims = buildClaims(spec, sources, taskIds);
  const contradictions =
    spec.status === 'completed' ? buildContradictions(spec, claims, sources) : [];

  for (const source of sources) {
    source.claim_count = claims.filter((claim) =>
      claim.supporting.some((e) => e.source_id === source.id),
    ).length;
  }

  const plan: ResearchPlan = {
    research_goal: spec.question,
    tasks,
    iteration: Math.max(...tasks.map((t) => t.iteration), 1),
  };

  const report = buildReport(spec, claims, sources, flagship);
  const answer = buildAnswer(spec, claims, contradictions, report);
  const activity = buildActivity(spec, plan, sources, claims);

  const totalTokens = activity.llm_calls.reduce((sum, call) => sum + call.total_tokens, 0);
  const cost = Number(activity.llm_calls.reduce((sum, call) => sum + call.cost_usd, 0).toFixed(4));
  const durationMs = runDurationMs(spec.mode);

  const run: ResearchRun = {
    id: spec.id,
    user_id: DEMO_USER_ID,
    parent_run_id: spec.parentRunId,
    title: spec.title,
    question: spec.question,
    mode: spec.mode,
    depth: spec.depth,
    domains: spec.domains,
    date_range_start: spec.dateRangeStart,
    date_range_end: spec.dateRangeEnd,
    status: spec.status,
    progress: spec.status === 'completed' ? 1 : 0,
    limits: limitsFor(spec.mode),
    usage: {
      iterations: plan.iteration,
      sources: sources.length,
      elapsed_seconds: Math.round(durationMs / 1000),
      total_tokens: totalTokens,
      cost_usd: cost,
    },
    source_count: sources.length,
    claim_count: claims.length,
    contradiction_count: contradictions.length,
    coverage_caveat: spec.coverageCaveat,
    has_report: report !== null,
    created_at: iso(spec.createdAt),
    started_at: spec.startedAt ? iso(spec.startedAt) : null,
    completed_at:
      spec.startedAt && spec.status !== 'queued' ? iso(shift(spec.startedAt, durationMs)) : null,
    error:
      spec.status === 'failed'
        ? {
            code: 'search_provider_unavailable',
            message: 'The search provider returned 503 on three consecutive attempts.',
          }
        : null,
  };

  return {
    spec,
    run,
    plan,
    sources,
    clusters,
    claims,
    contradictions,
    report,
    answer,
    activity,
    durationMs,
  };
}
