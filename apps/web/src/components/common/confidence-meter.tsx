import { cn } from '@/lib/utils';
import { formatPercent } from '@/lib/format';

/**
 * Confidence is shown as a value plus a band, never as a bare bar: a reader
 * needs to know that 0.58 is "low" without doing the arithmetic themselves.
 */
export type ConfidenceBand = 'low' | 'moderate' | 'high';

export function confidenceBand(value: number): ConfidenceBand {
  if (value >= 0.85) return 'high';
  if (value >= 0.65) return 'moderate';
  return 'low';
}

const BAND_STYLES: Record<ConfidenceBand, { bar: string; text: string; label: string }> = {
  high: { bar: 'bg-success', text: 'text-success', label: 'High' },
  moderate: { bar: 'bg-warning', text: 'text-warning', label: 'Moderate' },
  low: { bar: 'bg-destructive', text: 'text-destructive', label: 'Low' },
};

export function ConfidenceMeter({
  value,
  showLabel = true,
  className,
}: {
  value: number;
  showLabel?: boolean;
  className?: string;
}) {
  const band = confidenceBand(value);
  const styles = BAND_STYLES[band];

  return (
    <div className={cn('flex items-center gap-2', className)}>
      <div
        className="h-1.5 w-16 overflow-hidden rounded-full bg-muted"
        role="meter"
        aria-valuenow={Math.round(value * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Confidence"
      >
        <div
          className={cn('h-full rounded-full', styles.bar)}
          style={{ width: `${Math.max(2, Math.min(100, value * 100))}%` }}
        />
      </div>
      <span className={cn('font-mono text-xs tabular-nums', styles.text)}>
        {formatPercent(value)}
      </span>
      {showLabel ? <span className="text-xs text-muted-foreground">{styles.label}</span> : null}
    </div>
  );
}
