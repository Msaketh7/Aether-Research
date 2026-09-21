'use client';

import type { ResearchMode } from '@aether/shared-types';
import { LoaderCircle, X, Zap } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState, type FormEvent } from 'react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { ApiError } from '@/lib/api/client';
import { useCreateResearch } from '@/lib/api/queries';
import {
  NEW_RESEARCH_DEFAULTS,
  fieldErrorsOf,
  newResearchSchema,
  normalizeDomain,
  toCreateRequest,
  type NewResearchValues,
} from '@/lib/research/new-research-schema';
import { cn } from '@/lib/utils';

/**
 * The research creation form (FR-2).
 *
 * Validation runs locally for immediate feedback, then again on the server -
 * and the server's field errors are merged into the same display, so a rule the
 * client does not know about still lands on the right input.
 */

const MODES: Array<{
  value: Extract<ResearchMode, 'quick' | 'deep'>;
  title: string;
  description: string;
  meta: string;
}> = [
  {
    value: 'quick',
    title: 'Quick',
    description: 'One retrieval round, no critic loop. A short cited answer.',
    meta: 'Seconds · lower cost',
  },
  {
    value: 'deep',
    title: 'Deep',
    description:
      'Parallel researchers, evidence extraction, verification, a bounded critic loop and a full report.',
    meta: 'Minutes · full report',
  },
];

const DEPTHS = [
  { value: 1, label: 'Narrow', hint: 'Fewer subtasks, fewest sources' },
  { value: 2, label: 'Focused', hint: '' },
  { value: 3, label: 'Balanced', hint: 'Default breadth' },
  { value: 4, label: 'Broad', hint: '' },
  { value: 5, label: 'Exhaustive', hint: 'Most subtasks, up to the source ceiling' },
] as const;

export function NewResearchForm({ initialQuestion = '' }: { initialQuestion?: string }) {
  const router = useRouter();
  const createResearch = useCreateResearch();

  const [values, setValues] = useState<NewResearchValues>({
    ...NEW_RESEARCH_DEFAULTS,
    question: initialQuestion,
  });
  const [domainDraft, setDomainDraft] = useState('');
  const [clientErrors, setClientErrors] = useState<Record<string, string[]>>({});

  const apiError = createResearch.error instanceof ApiError ? createResearch.error : null;
  const errors: Record<string, string[]> = {
    ...clientErrors,
    ...(apiError?.details ?? {}),
  };
  const errorFor = (field: string) => errors[field]?.join(' ');

  const update = <K extends keyof NewResearchValues>(key: K, value: NewResearchValues[K]) => {
    setValues((current) => ({ ...current, [key]: value }));
    setClientErrors((current) => {
      if (!current[key as string]) return current;
      const next = { ...current };
      delete next[key as string];
      return next;
    });
  };

  const addDomain = () => {
    const domain = normalizeDomain(domainDraft);
    if (!domain) {
      setClientErrors((current) => ({
        ...current,
        domains: ['Enter a bare domain such as sec.gov.'],
      }));
      return;
    }
    if (!values.domains.includes(domain)) update('domains', [...values.domains, domain]);
    setDomainDraft('');
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const parsed = newResearchSchema.safeParse(values);
    if (!parsed.success) {
      setClientErrors(fieldErrorsOf(parsed.error));
      return;
    }
    setClientErrors({});
    createResearch.mutate(toCreateRequest(parsed.data), {
      onSuccess: (response) => router.push(`/research/${response.run_id}`),
    });
  };

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
      <Card>
        <CardHeader>
          <CardTitle>Research question</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          <Label htmlFor="question" className="sr-only">
            Research question
          </Label>
          <Textarea
            id="question"
            name="question"
            rows={5}
            placeholder="Compare the major AI inference infrastructure companies. Analyze their products, technology, pricing, funding, financial performance, recent announcements, risks, competitive advantages, and market opportunities."
            value={values.question}
            onChange={(event) => update('question', event.target.value)}
            aria-invalid={Boolean(errorFor('question'))}
            aria-describedby={errorFor('question') ? 'question-error' : undefined}
            data-testid="question-input"
          />
          {errorFor('question') ? (
            <p id="question-error" className="text-xs text-destructive-strong">
              {errorFor('question')}
            </p>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Mode</CardTitle>
        </CardHeader>
        <CardContent>
          <div role="radiogroup" aria-label="Research mode" className="grid gap-3 sm:grid-cols-2">
            {MODES.map((mode) => {
              const selected = values.mode === mode.value;
              return (
                <button
                  key={mode.value}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  data-testid={`mode-${mode.value}`}
                  onClick={() => update('mode', mode.value)}
                  className={cn(
                    'relative rounded-xl border p-4 text-left',
                    'transition-[border-color,background-color,box-shadow,transform] duration-[var(--duration-base)] ease-[var(--ease-out-soft)]',
                    'hover:-translate-y-0.5 active:translate-y-0',
                    selected
                      ? 'border-primary bg-accent/60 shadow-glow'
                      : 'border-border hover:border-primary/40 hover:shadow-e2',
                  )}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium">{mode.title}</span>
                    {mode.value === 'quick' ? (
                      <Zap className="size-3.5 text-muted-foreground" aria-hidden />
                    ) : null}
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                    {mode.description}
                  </p>
                  <p className="mt-2 font-mono text-[11px] text-muted-foreground">{mode.meta}</p>
                </button>
              );
            })}
          </div>

          {values.mode === 'deep' ? (
            <fieldset className="mt-5">
              <legend className="text-sm font-medium">Depth</legend>
              <p className="mt-1 text-xs text-muted-foreground">
                Controls how many subtasks the planner generates. Hard ceilings on iterations,
                sources, runtime and cost apply regardless of depth.
              </p>
              <div className="mt-2.5 flex flex-wrap gap-2" role="radiogroup" aria-label="Depth">
                {DEPTHS.map((depth) => {
                  const selected = values.depth === depth.value;
                  return (
                    <button
                      key={depth.value}
                      type="button"
                      role="radio"
                      aria-checked={selected}
                      data-testid={`depth-${depth.value}`}
                      onClick={() => update('depth', depth.value)}
                      className={cn(
                        'rounded-full border px-3.5 py-1.5 text-xs',
                        'transition-[border-color,background-color,color,transform] duration-[var(--duration-fast)] ease-[var(--ease-out-soft)]',
                        'active:scale-95',
                        selected
                          ? 'border-primary bg-primary/12 font-medium text-primary'
                          : 'border-border text-muted-foreground hover:border-primary/40 hover:text-foreground',
                      )}
                    >
                      {depth.label}
                    </button>
                  );
                })}
              </div>
              <p className="mt-2 text-xs text-muted-foreground">
                {DEPTHS.find((depth) => depth.value === values.depth)?.hint}
              </p>
            </fieldset>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Filters (optional)</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-5">
          <div className="flex flex-col gap-2">
            <Label htmlFor="domain">Domains</Label>
            <div className="flex gap-2">
              <Input
                id="domain"
                placeholder="sec.gov"
                value={domainDraft}
                onChange={(event) => setDomainDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    addDomain();
                  }
                }}
                aria-invalid={Boolean(errorFor('domains'))}
                aria-describedby={errorFor('domains') ? 'domains-error' : undefined}
                data-testid="domain-input"
              />
              <Button type="button" variant="outline" onClick={addDomain}>
                Add
              </Button>
            </div>
            {errorFor('domains') ? (
              <p id="domains-error" className="text-xs text-destructive-strong">
                {errorFor('domains')}
              </p>
            ) : null}
            {values.domains.length > 0 ? (
              <ul className="flex flex-wrap gap-1.5">
                {values.domains.map((domain) => (
                  <li key={domain}>
                    <Badge variant="outline" className="gap-1 pr-1">
                      {domain}
                      <button
                        type="button"
                        aria-label={`Remove ${domain}`}
                        onClick={() =>
                          update(
                            'domains',
                            values.domains.filter((item) => item !== domain),
                          )
                        }
                        className="rounded-full p-0.5 transition-colors duration-[var(--duration-fast)] hover:bg-destructive/15 hover:text-destructive-strong"
                      >
                        <X className="size-3" aria-hidden />
                      </button>
                    </Badge>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-2">
              <Label htmlFor="date-start">Published after</Label>
              <Input
                id="date-start"
                type="date"
                value={values.dateRangeStart ?? ''}
                onChange={(event) => update('dateRangeStart', event.target.value)}
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="date-end">Published before</Label>
              <Input
                id="date-end"
                type="date"
                value={values.dateRangeEnd ?? ''}
                onChange={(event) => update('dateRangeEnd', event.target.value)}
                aria-invalid={Boolean(errorFor('dateRangeEnd'))}
                aria-describedby={errorFor('dateRangeEnd') ? 'date-error' : undefined}
              />
              {errorFor('dateRangeEnd') ? (
                <p id="date-error" className="text-xs text-destructive-strong">
                  {errorFor('dateRangeEnd')}
                </p>
              ) : null}
            </div>
          </div>

          <p className="text-xs text-muted-foreground">
            Document upload is wired to the storage layer in Phase 4; the field appears here once it
            does something real.
          </p>
        </CardContent>
      </Card>

      {apiError && Object.keys(apiError.details ?? {}).length === 0 ? (
        <Alert variant="danger">
          <AlertDescription>{apiError.message}</AlertDescription>
        </Alert>
      ) : null}

      <div className="flex items-center gap-3">
        <Button type="submit" disabled={createResearch.isPending} data-testid="start-research">
          {createResearch.isPending ? (
            <LoaderCircle className="animate-spin" data-motion="loop" aria-hidden />
          ) : null}
          Start research
        </Button>
        <p className="text-xs text-muted-foreground">
          The run is queued immediately and executed by a worker, so you can close this tab.
        </p>
      </div>
    </form>
  );
}
