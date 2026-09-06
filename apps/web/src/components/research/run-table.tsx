'use client';

import type { ResearchRunSummary } from '@aether/shared-types';
import { AlertTriangle, FileText, Quote } from 'lucide-react';
import Link from 'next/link';
import { Progress } from '@/components/ui/progress';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { formatCost, formatRelativeTime, truncate } from '@/lib/format';
import { ModeBadge, RunStatusBadge } from './badges';

const ACTIVE = new Set([
  'queued',
  'planning',
  'researching',
  'verifying',
  'synthesizing',
  'validating',
]);

/** Research history. One row per run, newest first. */
export function RunTable({ runs }: { runs: ResearchRunSummary[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="min-w-64">Research</TableHead>
          <TableHead className="w-28">Status</TableHead>
          <TableHead className="w-24">Mode</TableHead>
          <TableHead className="w-40">Findings</TableHead>
          <TableHead className="w-20 text-right">Cost</TableHead>
          <TableHead className="w-28 text-right">Started</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {runs.map((run) => (
          <TableRow key={run.id} data-testid="run-row">
            <TableCell>
              <Link
                href={`/research/${run.id}`}
                className="block font-medium underline-offset-2 hover:underline"
              >
                {run.title}
              </Link>
              <p className="mt-0.5 text-xs text-muted-foreground">{truncate(run.question, 110)}</p>
              {ACTIVE.has(run.status) ? (
                <Progress
                  value={Math.round(run.progress * 100)}
                  className="mt-2 max-w-56"
                  aria-label="Run progress"
                />
              ) : null}
            </TableCell>
            <TableCell>
              <RunStatusBadge status={run.status} />
            </TableCell>
            <TableCell>
              <ModeBadge mode={run.mode} />
            </TableCell>
            <TableCell>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <span className="inline-flex items-center gap-1">
                  <FileText className="size-3" aria-hidden />
                  {run.source_count} sources
                </span>
                <span className="inline-flex items-center gap-1">
                  <Quote className="size-3" aria-hidden />
                  {run.claim_count} claims
                </span>
                {run.contradiction_count > 0 ? (
                  <span className="inline-flex items-center gap-1 text-warning">
                    <AlertTriangle className="size-3" aria-hidden />
                    {run.contradiction_count}
                  </span>
                ) : null}
              </div>
            </TableCell>
            <TableCell className="text-right font-mono text-xs tabular-nums">
              {formatCost(run.cost_usd)}
            </TableCell>
            <TableCell className="text-right text-xs text-muted-foreground">
              {formatRelativeTime(run.created_at)}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
