import type { LucideIcon } from 'lucide-react';
import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';

/** A single measured number. `value` is pre-formatted so an em dash can mean "not measured". */
export function StatTile({
  label,
  value,
  hint,
  icon: Icon,
  className,
}: {
  label: string;
  value: string;
  hint?: string;
  icon?: LucideIcon;
  className?: string;
}) {
  return (
    <Card
      className={cn(
        'group relative overflow-hidden p-4',
        'transition-[transform,box-shadow,border-color] duration-[var(--duration-base)] ease-[var(--ease-out-soft)]',
        'hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-e2',
        className,
      )}
    >
      {/* A tint that arrives on hover. Decorative, and behind the text. */}
      <span
        className="pointer-events-none absolute inset-0 bg-linear-to-br from-primary/6 to-transparent opacity-0 transition-opacity duration-[var(--duration-base)] group-hover:opacity-100"
        aria-hidden
      />

      <div className="relative flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-muted-foreground">{label}</p>
        {Icon ? (
          <Icon
            className="size-3.5 text-muted-foreground transition-[color,transform] duration-[var(--duration-base)] ease-[var(--ease-spring)] group-hover:scale-110 group-hover:text-primary"
            aria-hidden
          />
        ) : null}
      </div>

      <p className="relative mt-2 font-mono text-2xl font-semibold tabular-nums leading-none">
        {value}
      </p>
      {hint ? <p className="relative mt-1.5 text-xs text-muted-foreground">{hint}</p> : null}
    </Card>
  );
}
