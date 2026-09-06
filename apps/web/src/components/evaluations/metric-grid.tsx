import type { MetricValue } from '@aether/shared-types';
import { ArrowDown, ArrowUp, Minus } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { formatCost, formatDuration, formatPercent, NOT_MEASURED } from '@/lib/format';
import { cn } from '@/lib/utils';

/**
 * Renders measured metrics.
 *
 * A `null` value renders as an em dash labelled "not measured" - never as zero.
 * The distinction is the whole point of the evaluation surface: an unmeasured
 * metric and a metric measured at zero mean opposite things.
 */

function display(metric: MetricValue): string {
  if (metric.value === null) return NOT_MEASURED;
  switch (metric.unit) {
    case 'ratio':
      return formatPercent(metric.value, 1);
    case 'seconds':
      return formatDuration(metric.value);
    case 'usd':
      return formatCost(metric.value);
    default:
      return String(metric.value);
  }
}

function displayThreshold(metric: MetricValue): string | null {
  if (metric.threshold === null) return null;
  return metric.unit === 'ratio' ? formatPercent(metric.threshold, 0) : String(metric.threshold);
}

function DeltaBadge({ metric }: { metric: MetricValue }) {
  if (metric.delta === null || metric.delta === 0) {
    return (
      <span className="inline-flex items-center gap-0.5 text-xs text-muted-foreground">
        <Minus className="size-3" aria-hidden />
        no change
      </span>
    );
  }

  const improved = metric.delta > 0;
  const Icon = improved ? ArrowUp : ArrowDown;
  const magnitude =
    metric.unit === 'ratio' ? formatPercent(Math.abs(metric.delta), 1) : Math.abs(metric.delta);

  return (
    <span
      className={cn(
        'inline-flex items-center gap-0.5 text-xs',
        improved ? 'text-success' : 'text-warning',
      )}
    >
      <Icon className="size-3" aria-hidden />
      {magnitude} vs previous
    </span>
  );
}

export function MetricGrid({ title, metrics }: { title: string; metrics: MetricValue[] }) {
  return (
    <section aria-label={title} className="flex flex-col gap-3">
      <h3 className="text-sm font-semibold">{title}</h3>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {metrics.map((metric) => {
          const threshold = displayThreshold(metric);
          return (
            <Card key={metric.key} className="p-4" data-testid="metric-card">
              <div className="flex items-start justify-between gap-2">
                <p className="text-xs font-medium text-muted-foreground">{metric.label}</p>
                {metric.passed === null ? null : (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span
                        className={cn(
                          'cursor-help rounded-full px-1.5 py-0.5 text-[10px] font-medium',
                          metric.passed
                            ? 'bg-success/15 text-success'
                            : 'bg-destructive/15 text-destructive',
                        )}
                      >
                        {metric.passed ? 'pass' : 'fail'}
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>
                      Gate: at least {threshold}. A regression below this fails the build.
                    </TooltipContent>
                  </Tooltip>
                )}
              </div>

              <p className="mt-2 font-mono text-2xl font-semibold tabular-nums leading-none">
                {display(metric)}
              </p>

              <div className="mt-2 flex items-center justify-between gap-2">
                <DeltaBadge metric={metric} />
                {threshold ? (
                  <span className="font-mono text-[11px] text-muted-foreground">
                    gate {threshold}
                  </span>
                ) : null}
              </div>

              {metric.value === null ? (
                <p className="mt-1.5 text-[11px] text-muted-foreground">Not measured</p>
              ) : null}
            </Card>
          );
        })}
      </div>
    </section>
  );
}
