import type {
  ClaimStatus,
  ContradictionResolution,
  EvidenceStance,
  ResearchMode,
  RunStatus,
  SourceType,
  TaskPriority,
} from '@aether/shared-types';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { cn } from '@/lib/utils';

/**
 * Status vocabulary, in one place.
 *
 * Colour carries meaning across the whole product - amber always means
 * "needs a human decision", red always means "failed or refuted" - so the
 * mapping lives here rather than being re-decided per page.
 */

type Variant = NonNullable<BadgeProps['variant']>;

const RUN_STATUS: Record<RunStatus, { label: string; variant: Variant }> = {
  queued: { label: 'Queued', variant: 'outline' },
  planning: { label: 'Planning', variant: 'info' },
  researching: { label: 'Researching', variant: 'info' },
  verifying: { label: 'Verifying', variant: 'info' },
  synthesizing: { label: 'Synthesizing', variant: 'info' },
  validating: { label: 'Validating', variant: 'info' },
  paused: { label: 'Paused', variant: 'warning' },
  completed: { label: 'Completed', variant: 'success' },
  failed: { label: 'Failed', variant: 'danger' },
  cancelled: { label: 'Cancelled', variant: 'default' },
};

const ACTIVE_STATUSES: ReadonlySet<RunStatus> = new Set([
  'planning',
  'researching',
  'verifying',
  'synthesizing',
  'validating',
]);

export function RunStatusBadge({ status, className }: { status: RunStatus; className?: string }) {
  const config = RUN_STATUS[status];
  const active = ACTIVE_STATUSES.has(status);

  return (
    <Badge variant={config.variant} className={className} data-status={status}>
      {active ? (
        <span
          className="size-1.5 animate-pulse rounded-full bg-current"
          aria-hidden
          data-testid="status-pulse"
        />
      ) : null}
      {config.label}
    </Badge>
  );
}

const MODE_LABELS: Record<ResearchMode, string> = {
  quick: 'Quick',
  deep: 'Deep',
  conversational: 'Follow-up',
};

export function ModeBadge({ mode }: { mode: ResearchMode }) {
  return <Badge variant={mode === 'deep' ? 'primary' : 'outline'}>{MODE_LABELS[mode]}</Badge>;
}

const SOURCE_TYPE: Record<SourceType, { label: string; className: string }> = {
  web: { label: 'Web', className: 'text-muted-foreground' },
  sec: { label: 'SEC', className: 'text-info-strong' },
  arxiv: { label: 'arXiv', className: 'text-primary-strong' },
  github: { label: 'GitHub', className: 'text-foreground' },
  upload: { label: 'Upload', className: 'text-warning-strong' },
};

export function SourceTypeBadge({ type }: { type: SourceType }) {
  const config = SOURCE_TYPE[type];
  return (
    <Badge variant="outline" className={cn('font-mono text-[10px] uppercase', config.className)}>
      {config.label}
    </Badge>
  );
}

const CLAIM_STATUS: Record<ClaimStatus, { label: string; variant: Variant }> = {
  candidate: { label: 'Candidate', variant: 'outline' },
  verified: { label: 'Verified', variant: 'success' },
  refuted: { label: 'Refuted', variant: 'danger' },
  contested: { label: 'Contested', variant: 'warning' },
};

export function ClaimStatusBadge({ status }: { status: ClaimStatus }) {
  const config = CLAIM_STATUS[status];
  return <Badge variant={config.variant}>{config.label}</Badge>;
}

const STANCE: Record<EvidenceStance, { label: string; className: string }> = {
  supports: { label: 'Supports', className: 'text-success-strong' },
  refutes: { label: 'Refutes', className: 'text-destructive-strong' },
  neutral: { label: 'Neutral', className: 'text-muted-foreground' },
};

export function StanceLabel({ stance }: { stance: EvidenceStance }) {
  const config = STANCE[stance];
  return (
    <span className={cn('text-xs font-medium uppercase tracking-wide', config.className)}>
      {config.label}
    </span>
  );
}

const RESOLUTION: Record<ContradictionResolution, { label: string; variant: Variant }> = {
  unresolved: { label: 'Unresolved', variant: 'warning' },
  resolved_a: { label: 'Resolved to source A', variant: 'success' },
  resolved_b: { label: 'Resolved to source B', variant: 'success' },
  both_valid_in_context: { label: 'Both valid in context', variant: 'info' },
};

export function ResolutionBadge({ resolution }: { resolution: ContradictionResolution }) {
  const config = RESOLUTION[resolution];
  return <Badge variant={config.variant}>{config.label}</Badge>;
}

const PRIORITY: Record<TaskPriority, Variant> = {
  high: 'primary',
  medium: 'default',
  low: 'outline',
};

export function PriorityBadge({ priority }: { priority: TaskPriority }) {
  return (
    <Badge variant={PRIORITY[priority]} className="capitalize">
      {priority}
    </Badge>
  );
}
