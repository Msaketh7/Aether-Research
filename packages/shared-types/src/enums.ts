/**
 * Closed vocabularies.
 *
 * Each is declared once as a `const` array so it can be iterated at runtime
 * (filter dropdowns, mock generators, tests) and narrowed at compile time. The
 * values match the Postgres `text` check constraints in docs/TDD.md section 7.2.
 */

export const RESEARCH_MODES = ['quick', 'deep', 'conversational'] as const;
export type ResearchMode = (typeof RESEARCH_MODES)[number];

/**
 * Run lifecycle. The first eight values are the workflow phases from TDD 7.2;
 * `paused` is the Phase 13 job state, surfaced to the user because a paused run
 * is resumable and must not look like a failure.
 */
export const RUN_STATUSES = [
  'queued',
  'planning',
  'researching',
  'verifying',
  'synthesizing',
  'validating',
  'paused',
  'completed',
  'failed',
  'cancelled',
] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];

/** Statuses that mean the run is over and will not change again. */
export const TERMINAL_RUN_STATUSES = ['completed', 'failed', 'cancelled'] as const;
export type TerminalRunStatus = (typeof TERMINAL_RUN_STATUSES)[number];

export const TASK_STATUSES = ['pending', 'researching', 'done', 'insufficient'] as const;
export type TaskStatus = (typeof TASK_STATUSES)[number];

export const TASK_PRIORITIES = ['high', 'medium', 'low'] as const;
export type TaskPriority = (typeof TASK_PRIORITIES)[number];

export const SOURCE_TYPES = ['web', 'sec', 'arxiv', 'github', 'upload'] as const;
export type SourceType = (typeof SOURCE_TYPES)[number];

/** What an uploaded or fetched document was read as (Phase 7). */
export const DOCUMENT_FORMATS = ['pdf', 'html', 'markdown', 'text'] as const;
export type DocumentFormat = (typeof DOCUMENT_FORMATS)[number];

export const CLAIM_TYPES = ['quantitative', 'qualitative', 'event'] as const;
export type ClaimType = (typeof CLAIM_TYPES)[number];

export const CLAIM_STATUSES = ['candidate', 'verified', 'refuted', 'contested'] as const;
export type ClaimStatus = (typeof CLAIM_STATUSES)[number];

export const EVIDENCE_STANCES = ['supports', 'refutes', 'neutral'] as const;
export type EvidenceStance = (typeof EVIDENCE_STANCES)[number];

export const CONTRADICTION_RESOLUTIONS = [
  'unresolved',
  'resolved_a',
  'resolved_b',
  'both_valid_in_context',
] as const;
export type ContradictionResolution = (typeof CONTRADICTION_RESOLUTIONS)[number];

export const REPORT_STATUSES = ['draft', 'validated', 'published'] as const;
export type ReportStatus = (typeof REPORT_STATUSES)[number];

/** Report section order is the order of this array (FR-9). */
export const REPORT_SECTION_KINDS = [
  'executive_summary',
  'key_findings',
  'detailed_analysis',
  'competitive_landscape',
  'evidence',
  'contradictions',
  'confidence_assessment',
  'recommendations',
  'references',
] as const;
export type ReportSectionKind = (typeof REPORT_SECTION_KINDS)[number];

export const AGENT_NAMES = [
  'planner',
  'researcher',
  'evidence_extractor',
  'claim_normalizer',
  'verifier',
  'critic',
  'answerer',
  'synthesizer',
  'citation_validator',
] as const;
export type AgentName = (typeof AGENT_NAMES)[number];

export const AGENT_STATUSES = ['running', 'ok', 'error'] as const;
export type AgentStatus = (typeof AGENT_STATUSES)[number];

export const TOOL_NAMES = [
  'search',
  'fetch',
  'parse',
  'retrieve',
  'sec_api',
  'arxiv_api',
  'github_api',
] as const;
export type ToolName = (typeof TOOL_NAMES)[number];

export const TOOL_STATUSES = ['ok', 'error', 'rate_limited', 'timeout'] as const;
export type ToolStatus = (typeof TOOL_STATUSES)[number];

export const LLM_PROVIDERS = ['openai', 'anthropic', 'gemini', 'ollama'] as const;
export type LlmProvider = (typeof LLM_PROVIDERS)[number];

export const LLM_CALL_STATUSES = ['ok', 'error', 'fallback'] as const;
export type LlmCallStatus = (typeof LLM_CALL_STATUSES)[number];

export const EVALUATION_KINDS = [
  'retrieval',
  'generation',
  'agent',
  'infrastructure',
  'full',
] as const;
export type EvaluationKind = (typeof EVALUATION_KINDS)[number];

export const FEEDBACK_CATEGORIES = [
  'accuracy',
  'completeness',
  'citations',
  'readability',
  'other',
] as const;
export type FeedbackCategory = (typeof FEEDBACK_CATEGORIES)[number];
