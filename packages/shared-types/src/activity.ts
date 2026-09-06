import type { IsoDateTime, Uuid } from './common';
import type {
  AgentName,
  AgentStatus,
  LlmCallStatus,
  LlmProvider,
  ToolName,
  ToolStatus,
} from './enums';

/** One agent execution. The unit of the step-by-step trace. */
export interface AgentRunRecord {
  id: Uuid;
  run_id: Uuid;
  task_external_id: string | null;
  agent_name: AgentName;
  iteration: number;
  status: AgentStatus;
  /** One-line human summary, e.g. "decomposed into 7 subtasks". */
  summary: string;
  latency_ms: number | null;
  tokens: number;
  cost_usd: number;
  /** OpenTelemetry ids, so a UI row links to a trace in Phase 17. */
  trace_id: string | null;
  span_id: string | null;
  error: { code: string; message: string } | null;
  started_at: IsoDateTime;
  completed_at: IsoDateTime | null;
}

/** One external action taken by an agent: a search, a fetch, an API call. */
export interface ToolCallRecord {
  id: Uuid;
  agent_run_id: Uuid;
  tool_name: ToolName;
  /** Compact display of the request, e.g. the query or the URL. */
  request_summary: string;
  status: ToolStatus;
  latency_ms: number;
  cache_hit: boolean;
  retries: number;
  /** Short outcome, e.g. "12 results" or "403 forbidden". */
  result_summary: string;
  started_at: IsoDateTime;
}

/** One model call, with the accounting Phase 16 requires. */
export interface LlmCallRecord {
  id: Uuid;
  agent_run_id: Uuid | null;
  role: AgentName;
  provider: LlmProvider;
  model: string;
  prompt_version: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd: number;
  latency_ms: number;
  cache_hit: boolean;
  status: LlmCallStatus;
  created_at: IsoDateTime;
}

/** The full trace behind /research/[id]/activity. */
export interface ActivityResponse {
  agent_runs: AgentRunRecord[];
  tool_calls: ToolCallRecord[];
  llm_calls: LlmCallRecord[];
}
