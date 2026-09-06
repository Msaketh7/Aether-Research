'use client';

import type { AgentRunRecord, ToolCallRecord } from '@aether/shared-types';
import { Activity, Bot, Cpu, Wrench } from 'lucide-react';
import { EmptyState } from '@/components/common/empty-state';
import { ErrorState } from '@/components/common/error-state';
import { SectionCard } from '@/components/common/section-card';
import { EventFeed } from '@/components/research/event-feed';
import { useRunContext } from '@/components/research/run-context';
import { StageChecklist } from '@/components/research/stage-checklist';
import { Badge } from '@/components/ui/badge';
import { Card } from '@/components/ui/card';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Skeleton } from '@/components/ui/skeleton';
import { useResearchActivity } from '@/lib/api/queries';
import { formatCost, formatDateTime, formatLatency, formatTokens, truncate } from '@/lib/format';

/**
 * The full agent trace (FR-10).
 *
 * Three linked layers: which agent ran, which external calls it made, and which
 * model calls it cost. Together they answer "why did the run do that?" without
 * needing the server logs.
 */

const AGENT_LABELS: Record<string, string> = {
  planner: 'Planner',
  researcher: 'Researcher',
  evidence_extractor: 'Evidence extractor',
  claim_normalizer: 'Claim normalizer',
  verifier: 'Verifier',
  critic: 'Critic',
  synthesizer: 'Synthesizer',
  citation_validator: 'Citation validator',
};

const TOOL_STATUS_VARIANT = {
  ok: 'success',
  error: 'danger',
  rate_limited: 'warning',
  timeout: 'warning',
} as const;

function AgentRow({ agent, tools }: { agent: AgentRunRecord; tools: ToolCallRecord[] }) {
  return (
    <Card className="p-4" data-testid="agent-run">
      <div className="flex flex-wrap items-center gap-2">
        <Bot className="size-4 text-primary" aria-hidden />
        <span className="text-sm font-medium">
          {AGENT_LABELS[agent.agent_name] ?? agent.agent_name}
        </span>
        {agent.task_external_id ? (
          <Badge variant="outline" className="font-mono text-[10px]">
            {agent.task_external_id}
          </Badge>
        ) : null}
        <Badge variant={agent.status === 'error' ? 'danger' : 'default'}>{agent.status}</Badge>
        {agent.iteration > 1 ? <Badge variant="outline">iteration {agent.iteration}</Badge> : null}
        <span className="ml-auto font-mono text-xs tabular-nums text-muted-foreground">
          {formatLatency(agent.latency_ms)} · {formatTokens(agent.tokens)} tok ·{' '}
          {formatCost(agent.cost_usd)}
        </span>
      </div>

      <p className="mt-1.5 text-sm text-muted-foreground">{agent.summary}</p>

      {agent.error ? (
        <p className="mt-2 rounded-md bg-destructive/8 p-2 text-xs text-destructive">
          <span className="font-medium">{agent.error.code}</span> — {agent.error.message}
        </p>
      ) : null}

      {tools.length > 0 ? (
        <ul className="mt-3 flex flex-col gap-1.5">
          {tools.map((tool) => (
            <li
              key={tool.id}
              className="flex flex-wrap items-center gap-2 border-l-2 border-border pl-3 text-xs"
              data-testid="tool-call"
            >
              <Wrench className="size-3 text-muted-foreground" aria-hidden />
              <span className="font-mono">{tool.tool_name}</span>
              <span className="min-w-0 flex-1 truncate text-muted-foreground">
                {truncate(tool.request_summary, 90)}
              </span>
              {tool.cache_hit ? <Badge variant="info">cache</Badge> : null}
              {tool.retries > 0 ? <Badge variant="warning">{tool.retries} retries</Badge> : null}
              <Badge variant={TOOL_STATUS_VARIANT[tool.status]}>{tool.status}</Badge>
              <span className="font-mono tabular-nums text-muted-foreground">
                {formatLatency(tool.latency_ms)}
              </span>
            </li>
          ))}
        </ul>
      ) : null}

      <p className="mt-2.5 font-mono text-[11px] text-muted-foreground/70">
        trace {agent.trace_id ?? '—'} · started {formatDateTime(agent.started_at)}
      </p>
    </Card>
  );
}

export default function RunActivityPage() {
  const { runId, events, stages } = useRunContext();
  const activity = useResearchActivity(runId);

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_20rem]">
      <div className="flex flex-col gap-5">
        <SectionCard title="Agent trace" contentClassName="flex flex-col gap-3">
          {activity.isPending ? (
            Array.from({ length: 5 }).map((_, index) => <Skeleton key={index} className="h-24" />)
          ) : activity.isError ? (
            <ErrorState error={activity.error} onRetry={() => void activity.refetch()} />
          ) : activity.data.agent_runs.length === 0 ? (
            <EmptyState
              icon={Activity}
              title="No agent activity yet"
              description="Agent executions appear here as soon as the planner starts."
            />
          ) : (
            activity.data.agent_runs.map((agent) => (
              <AgentRow
                key={agent.id}
                agent={agent}
                tools={activity.data.tool_calls.filter((tool) => tool.agent_run_id === agent.id)}
              />
            ))
          )}
        </SectionCard>

        {activity.data && activity.data.llm_calls.length > 0 ? (
          <SectionCard title="Model calls" contentClassName="px-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Role</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead>Prompt</TableHead>
                  <TableHead className="text-right">Tokens</TableHead>
                  <TableHead className="text-right">Latency</TableHead>
                  <TableHead className="text-right">Cost</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {activity.data.llm_calls.map((call) => (
                  <TableRow key={call.id} data-testid="llm-call">
                    <TableCell className="text-xs">
                      {AGENT_LABELS[call.role] ?? call.role}
                    </TableCell>
                    <TableCell className="font-mono text-xs">
                      <span className="inline-flex items-center gap-1">
                        <Cpu className="size-3 text-muted-foreground" aria-hidden />
                        {call.model}
                      </span>
                    </TableCell>
                    <TableCell className="font-mono text-[11px] text-muted-foreground">
                      {call.prompt_version}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs tabular-nums">
                      {formatTokens(call.total_tokens)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs tabular-nums">
                      {formatLatency(call.latency_ms)}
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs tabular-nums">
                      {formatCost(call.cost_usd)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </SectionCard>
        ) : null}
      </div>

      <div className="flex flex-col gap-5">
        <SectionCard title="Stages">
          <StageChecklist stages={stages} />
        </SectionCard>
        <SectionCard title="Event stream" contentClassName="px-2">
          <EventFeed
            events={events}
            maxHeight="max-h-[32rem]"
            emptyMessage="No live events. This run has already finished; its trace is on the left."
          />
        </SectionCard>
      </div>
    </div>
  );
}
