import type { ResearchRun } from '@aether/shared-types';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { formatCost, formatDuration } from '@/lib/format';
import { cn } from '@/lib/utils';

/**
 * Consumption against the FR-8 ceilings.
 *
 * Budget is a first-class part of the product, not an admin detail: a user who
 * cannot see how close a run is to its cost and time limits cannot understand
 * why it stopped early.
 */

interface Row {
  label: string;
  used: number;
  limit: number;
  display: string;
  limitDisplay: string;
}

function ratio(used: number, limit: number): number {
  if (limit <= 0) return 0;
  return Math.max(0, Math.min(1, used / limit));
}

function barColor(value: number): string {
  if (value >= 0.9) return 'bg-destructive';
  if (value >= 0.7) return 'bg-warning';
  return 'bg-primary';
}

export function UsageMeter({ run, className }: { run: ResearchRun; className?: string }) {
  const rows: Row[] = [
    {
      label: 'Iterations',
      used: run.usage.iterations,
      limit: run.limits.max_iterations,
      display: String(run.usage.iterations),
      limitDisplay: String(run.limits.max_iterations),
    },
    {
      label: 'Sources',
      used: run.usage.sources,
      limit: run.limits.max_sources,
      display: String(run.usage.sources),
      limitDisplay: String(run.limits.max_sources),
    },
    {
      label: 'Runtime',
      used: run.usage.elapsed_seconds,
      limit: run.limits.max_runtime_seconds,
      display: formatDuration(run.usage.elapsed_seconds),
      limitDisplay: formatDuration(run.limits.max_runtime_seconds),
    },
    {
      label: 'Cost',
      used: run.usage.cost_usd,
      limit: run.limits.max_cost_usd,
      display: formatCost(run.usage.cost_usd),
      limitDisplay: formatCost(run.limits.max_cost_usd),
    },
  ];

  return (
    <dl className={cn('flex flex-col gap-3', className)}>
      {rows.map((row) => {
        const value = ratio(row.used, row.limit);
        return (
          <div key={row.label} className="flex flex-col gap-1">
            <div className="flex items-baseline justify-between gap-2">
              <dt className="text-xs text-muted-foreground">{row.label}</dt>
              <dd className="font-mono text-xs tabular-nums">
                {row.display}
                <span className="text-muted-foreground"> / {row.limitDisplay}</span>
              </dd>
            </div>
            <Tooltip>
              <TooltipTrigger asChild>
                <div
                  className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
                  role="meter"
                  aria-label={`${row.label} budget`}
                  aria-valuenow={Math.round(value * 100)}
                  aria-valuemin={0}
                  aria-valuemax={100}
                >
                  <div
                    className={cn('h-full rounded-full transition-all', barColor(value))}
                    style={{ width: `${Math.max(1, value * 100)}%` }}
                  />
                </div>
              </TooltipTrigger>
              <TooltipContent>
                {Math.round(value * 100)}% of the {row.label.toLowerCase()} ceiling. Reaching a
                ceiling ends the run safely with a partial report.
              </TooltipContent>
            </Tooltip>
          </div>
        );
      })}
    </dl>
  );
}
