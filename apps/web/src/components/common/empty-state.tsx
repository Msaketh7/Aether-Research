import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';

/**
 * Shown when a query succeeded and returned nothing. Distinct from the error
 * state on purpose: "no sources yet" and "we could not load sources" are
 * different facts and must never render the same way.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border px-6 py-14 text-center">
      <Icon className="size-5 text-muted-foreground" aria-hidden />
      <p className="text-sm font-medium">{title}</p>
      <p className="max-w-md text-sm text-muted-foreground">{description}</p>
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}
