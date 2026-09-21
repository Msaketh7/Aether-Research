'use client';

import type { ResearchMode } from '@aether/shared-types';
import {
  CalendarRange,
  ChevronRight,
  Globe,
  Plus,
  SlidersHorizontal,
  Telescope,
  X,
} from 'lucide-react';
import Link from 'next/link';
import { useId, useState, type KeyboardEvent, type ReactNode } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { normalizeDomain } from '@/lib/research/new-research-schema';
import { cn } from '@/lib/utils';

/**
 * Depth and filters for the composer.
 *
 * Three collapsed rows rather than one open panel. Every one of these settings
 * is optional and most runs use none of them, so showing all three expanded
 * puts four controls and six labels in front of somebody who came to type a
 * question. Collapsed, each row states its own current value, which is the
 * thing worth knowing at a glance; opening one is a deliberate act.
 *
 * Only one row is open at a time. Two open sections in a panel this size means
 * scrolling inside a floating card, which is worse than a second click.
 */

export interface ComposerFilterValues {
  depth: number;
  domains: string[];
  dateRangeStart: string;
  dateRangeEnd: string;
}

export const EMPTY_FILTERS: ComposerFilterValues = {
  depth: 3,
  domains: [],
  dateRangeStart: '',
  dateRangeEnd: '',
};

const DEPTHS = [
  { value: 1, label: 'Narrow', hint: 'Fewest subtasks and sources' },
  { value: 2, label: 'Focused', hint: 'Fewer subtasks than the default' },
  { value: 3, label: 'Balanced', hint: 'Default breadth' },
  { value: 4, label: 'Broad', hint: 'More subtasks than the default' },
  { value: 5, label: 'Exhaustive', hint: 'Most subtasks, up to the source ceiling' },
] as const;

type Section = 'depth' | 'domains' | 'dates';

/** How many choices differ from the defaults. Depth only counts in deep mode. */
export function activeFilterCount(values: ComposerFilterValues, mode: ResearchMode): number {
  return (
    (mode === 'deep' && values.depth !== EMPTY_FILTERS.depth ? 1 : 0) +
    (values.domains.length > 0 ? 1 : 0) +
    (values.dateRangeStart || values.dateRangeEnd ? 1 : 0)
  );
}

function shortDate(value: string): string {
  if (!value) return '';
  const date = new Date(`${value}T00:00:00`);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' });
}

/**
 * One collapsible row: a chevron, an icon, the name, and the value it holds.
 */
function Row({
  icon: Icon,
  label,
  summary,
  open,
  onToggle,
  children,
  action,
}: {
  icon: typeof Globe;
  label: string;
  summary: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
  action?: ReactNode;
}) {
  const panelId = useId();

  return (
    <div className="border-b border-border/70 last:border-0">
      <div className="flex items-center gap-1">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          aria-controls={panelId}
          className={cn(
            'flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-2 py-2.5 text-left',
            'transition-colors duration-[var(--duration-fast)] ease-[var(--ease-out-soft)]',
            'hover:bg-hover',
          )}
        >
          <ChevronRight
            className={cn(
              'size-3.5 shrink-0 text-muted-foreground',
              'transition-transform duration-[var(--duration-base)] ease-[var(--ease-out-quick)]',
              open && 'rotate-90',
            )}
            aria-hidden
          />
          <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <span className="shrink-0 text-xs font-medium">{label}</span>
          <span className="min-w-0 flex-1 truncate text-right text-xs text-muted-foreground">
            {summary}
          </span>
        </button>
        {action}
      </div>

      {/*
       * A grid row collapsing from 1fr to 0fr is the one way to animate "as
       * tall as its content" without measuring it in JavaScript or hard-coding
       * a height that a wrapped chip row would then overflow.
       *
       * `visibility` is in the transition on purpose. Clipping with
       * `overflow: hidden` hides a collapsed row from the eye but leaves its
       * buttons in the tab order and in the accessibility tree, so Tab landed
       * on depth chips nobody could see. Visibility takes them out, and because
       * it flips at the end of a transition out and at the start of one in, the
       * animation still plays in both directions.
       */}
      <div
        id={panelId}
        className={cn(
          'grid transition-[grid-template-rows,opacity,visibility] duration-[var(--duration-base)] ease-[var(--ease-out-quick)]',
          open ? 'visible grid-rows-[1fr] opacity-100' : 'invisible grid-rows-[0fr] opacity-0',
        )}
      >
        <div className="overflow-hidden">
          <div className="px-2 pb-3 pt-0.5">{children}</div>
        </div>
      </div>
    </div>
  );
}

export function ComposerFilters({
  mode,
  values,
  onChange,
  disabled = false,
}: {
  mode: ResearchMode;
  values: ComposerFilterValues;
  onChange: (next: ComposerFilterValues) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState<Section | null>(null);
  const [draft, setDraft] = useState('');
  const [domainError, setDomainError] = useState<string | null>(null);
  const count = activeFilterCount(values, mode);

  const toggle = (section: Section) => setOpen((current) => (current === section ? null : section));

  const set = <K extends keyof ComposerFilterValues>(key: K, value: ComposerFilterValues[K]) =>
    onChange({ ...values, [key]: value });

  const addDomain = () => {
    if (!draft.trim()) return;
    const domain = normalizeDomain(draft);
    if (!domain) {
      setDomainError('Enter a bare domain such as sec.gov.');
      return;
    }
    setDomainError(null);
    setDraft('');
    if (!values.domains.includes(domain)) set('domains', [...values.domains, domain]);
  };

  // The popover is portalled outside the composer's form, so Enter here can
  // never submit the question by accident, but it still has to do the obvious
  // thing, which is add the domain.
  const onDomainKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    addDomain();
  };

  const depthLabel = DEPTHS.find((depth) => depth.value === values.depth)?.label ?? 'Balanced';
  const dateSummary =
    values.dateRangeStart && values.dateRangeEnd
      ? `${shortDate(values.dateRangeStart)} to ${shortDate(values.dateRangeEnd)}`
      : values.dateRangeStart
        ? `After ${shortDate(values.dateRangeStart)}`
        : values.dateRangeEnd
          ? `Before ${shortDate(values.dateRangeEnd)}`
          : 'Any time';

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={disabled}
          aria-label={count > 0 ? `Filters and depth, ${count} applied` : 'Filters and depth'}
          className={cn('gap-1.5', count > 0 ? 'text-foreground' : 'text-muted-foreground')}
        >
          <SlidersHorizontal aria-hidden />
          <span className="hidden sm:inline">Filters</span>
          {count > 0 ? (
            <Badge variant="primary" className="px-1.5 py-0 text-[10px] tabular-nums">
              {count}
            </Badge>
          ) : null}
        </Button>
      </PopoverTrigger>

      <PopoverContent align="start" className="w-80 p-2" data-testid="composer-filters">
        <div className="flex items-center justify-between gap-2 px-2 pb-1 pt-1">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Depth and filters
          </p>
          {count > 0 ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs text-muted-foreground"
              onClick={() => {
                onChange(EMPTY_FILTERS);
                setDraft('');
                setDomainError(null);
              }}
            >
              Clear
            </Button>
          ) : null}
        </div>

        <Row
          icon={Telescope}
          label="Depth"
          summary={mode === 'deep' ? depthLabel : 'Deep runs only'}
          open={open === 'depth'}
          onToggle={() => toggle('depth')}
        >
          {mode === 'deep' ? (
            <>
              <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Depth">
                {DEPTHS.map((depth) => {
                  const selected = values.depth === depth.value;
                  return (
                    <button
                      key={depth.value}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      title={depth.hint}
                      data-testid={`depth-${depth.value}`}
                      onClick={() => set('depth', depth.value)}
                      className={cn(
                        'rounded-full border px-2.5 py-1 text-xs',
                        'transition-[border-color,background-color,color,transform] duration-[var(--duration-fast)] ease-[var(--ease-out-soft)]',
                        'active:scale-95',
                        selected
                          ? 'border-primary bg-primary/15 font-medium text-primary'
                          : 'border-border text-muted-foreground hover:border-primary/45 hover:bg-hover hover:text-foreground',
                      )}
                    >
                      {depth.label}
                    </button>
                  );
                })}
              </div>
              <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                How many subtasks the planner writes. Ceilings on iterations, sources, runtime and
                cost apply at every depth.
              </p>
            </>
          ) : (
            <p className="text-xs leading-relaxed text-muted-foreground">
              A quick run is a single retrieval round, so there is no breadth to set. Switch to Deep
              to choose one.
            </p>
          )}
        </Row>

        <Row
          icon={Globe}
          label="Domains"
          summary={
            values.domains.length === 0
              ? 'Anywhere'
              : values.domains.length === 1
                ? (values.domains[0] ?? '')
                : `${values.domains.length} sites`
          }
          open={open === 'domains'}
          onToggle={() => toggle('domains')}
          action={
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-7 shrink-0 text-muted-foreground"
              aria-label="Add a domain"
              onClick={() => setOpen('domains')}
            >
              <Plus className="size-3.5" aria-hidden />
            </Button>
          }
        >
          <div className="flex gap-2">
            <Label htmlFor="composer-domain" className="sr-only">
              Domain
            </Label>
            <Input
              id="composer-domain"
              placeholder="sec.gov"
              value={draft}
              onChange={(event) => {
                setDraft(event.target.value);
                if (domainError) setDomainError(null);
              }}
              onKeyDown={onDomainKeyDown}
              aria-invalid={Boolean(domainError)}
              aria-describedby={domainError ? 'composer-domain-error' : undefined}
              data-testid="composer-domain-input"
              className="h-8"
            />
            <Button type="button" variant="outline" size="sm" onClick={addDomain}>
              Add
            </Button>
          </div>
          {domainError ? (
            <p
              id="composer-domain-error"
              role="alert"
              className="mt-2 text-xs text-destructive-strong"
            >
              {domainError}
            </p>
          ) : null}
          {values.domains.length > 0 ? (
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {values.domains.map((domain) => (
                <li key={domain}>
                  <Badge variant="outline" className="gap-1 pr-1">
                    {domain}
                    <button
                      type="button"
                      aria-label={`Remove ${domain}`}
                      onClick={() =>
                        set(
                          'domains',
                          values.domains.filter((item) => item !== domain),
                        )
                      }
                      className="rounded-full p-0.5 transition-colors duration-[var(--duration-fast)] hover:bg-destructive/20 hover:text-destructive-strong"
                    >
                      <X className="size-3" aria-hidden />
                    </button>
                  </Badge>
                </li>
              ))}
            </ul>
          ) : null}
        </Row>

        <Row
          icon={CalendarRange}
          label="Published"
          summary={dateSummary}
          open={open === 'dates'}
          onToggle={() => toggle('dates')}
        >
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="composer-date-start" className="text-xs text-muted-foreground">
                After
              </Label>
              <Input
                id="composer-date-start"
                type="date"
                value={values.dateRangeStart}
                onChange={(event) => set('dateRangeStart', event.target.value)}
                className="h-8"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="composer-date-end" className="text-xs text-muted-foreground">
                Before
              </Label>
              <Input
                id="composer-date-end"
                type="date"
                value={values.dateRangeEnd}
                onChange={(event) => set('dateRangeEnd', event.target.value)}
                className="h-8"
              />
            </div>
          </div>
        </Row>

        <p className="px-2 pb-1 pt-3 text-xs text-muted-foreground">
          Composing something longer?{' '}
          <Link href="/research/new" className="underline-grow text-foreground underline-offset-2">
            Open the full form
          </Link>
          .
        </p>
      </PopoverContent>
    </Popover>
  );
}
