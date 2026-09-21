'use client';

import type { ResearchMode } from '@aether/shared-types';
import { ArrowRight, ArrowUpRight, LoaderCircle, Search, Telescope, Zap } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useId, useState, type FormEvent, type KeyboardEvent } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { ApiError } from '@/lib/api/client';
import { useCreateResearch } from '@/lib/api/queries';
import { newResearchSchema, toCreateRequest } from '@/lib/research/new-research-schema';
import { cn } from '@/lib/utils';
import { ComposerFilters, EMPTY_FILTERS, type ComposerFilterValues } from './composer-filters';

/**
 * The single question box.
 *
 * A deep-research product is a question box first and an application second,
 * so the shortest path from opening the app to a running job is one field and
 * one key. Everything optional - depth, domains, dates - is one click away in
 * a card beside the question rather than on a screen of its own, because
 * leaving the page to add a date filter means abandoning a half-typed
 * question.
 *
 * The full form at /research/new is still there and still owns the considered
 * path. Both validate with the same `newResearchSchema`, so the rules cannot
 * differ between the two.
 */

const MODES: Array<{
  value: Extract<ResearchMode, 'quick' | 'deep'>;
  label: string;
  hint: string;
  icon: typeof Zap;
}> = [
  {
    value: 'deep',
    label: 'Deep',
    hint: 'Parallel researchers, critic loop, full cited report',
    icon: Telescope,
  },
  { value: 'quick', label: 'Quick', hint: 'One retrieval round, a short cited answer', icon: Zap },
];

export interface Suggestion {
  label: string;
  question: string;
}

export function AskComposer({
  autoFocus = false,
  suggestions,
  className,
}: {
  autoFocus?: boolean;
  suggestions?: readonly Suggestion[];
  className?: string;
}) {
  const router = useRouter();
  const createResearch = useCreateResearch();
  const [question, setQuestion] = useState('');
  const [mode, setMode] = useState<'quick' | 'deep'>('deep');
  const [filters, setFilters] = useState<ComposerFilterValues>(EMPTY_FILTERS);
  const [error, setError] = useState<string | null>(null);
  const errorId = useId();

  const pending = createResearch.isPending;

  const submit = () => {
    const parsed = newResearchSchema.safeParse({ question, mode, ...filters });

    if (!parsed.success) {
      setError(parsed.error.issues[0]?.message ?? 'Check the question and try again.');
      return;
    }

    setError(null);
    createResearch.mutate(toCreateRequest(parsed.data), {
      onSuccess: (response) => router.push(`/research/${response.run_id}`),
      onError: (err) =>
        setError(err instanceof ApiError ? err.message : 'Could not start the run. Try again.'),
    });
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    submit();
  };

  // Enter submits, Shift+Enter breaks the line: the convention every chat
  // assistant has trained people on. A multi-paragraph question is rare enough
  // that the modifier is the right way round.
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className={className}>
      <form
        onSubmit={onSubmit}
        className={cn(
          'rounded-2xl border border-border bg-card/80 p-2 shadow-e2 backdrop-blur-sm',
          'transition-[border-color,box-shadow] duration-[var(--duration-base)] ease-[var(--ease-out-soft)]',
          'focus-within:border-primary/45 focus-within:shadow-glow',
        )}
        noValidate
      >
        <label htmlFor="ask" className="sr-only">
          Research question
        </label>
        <Textarea
          id="ask"
          name="question"
          rows={3}
          autoFocus={autoFocus}
          value={question}
          onChange={(event) => {
            setQuestion(event.target.value);
            if (error) setError(null);
          }}
          onKeyDown={onKeyDown}
          disabled={pending}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? errorId : undefined}
          data-testid="ask-input"
          placeholder="Ask a complex question. Aether will decompose it, research the parts in parallel and write a cited report."
          className="min-h-20 resize-none border-0 bg-transparent px-3 py-2.5 text-base shadow-none focus-visible:shadow-none sm:text-sm"
        />

        <div className="flex flex-wrap items-center gap-2 px-1 pb-1 pt-1">
          <div
            role="radiogroup"
            aria-label="Research mode"
            className="flex items-center gap-1 rounded-full bg-muted/70 p-0.5"
          >
            {MODES.map(({ value, label, hint, icon: Icon }) => {
              const selected = mode === value;
              return (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={selected}
                  title={hint}
                  data-testid={`ask-mode-${value}`}
                  onClick={() => setMode(value)}
                  className={cn(
                    'inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium',
                    'transition-[background-color,color,box-shadow] duration-[var(--duration-fast)] ease-[var(--ease-out-soft)]',
                    selected
                      ? 'bg-card text-foreground shadow-e1'
                      : 'text-muted-foreground hover:text-foreground',
                  )}
                >
                  <Icon className="size-3.5" aria-hidden />
                  {label}
                </button>
              );
            })}
          </div>

          <ComposerFilters mode={mode} values={filters} onChange={setFilters} disabled={pending} />

          <div className="ml-auto flex items-center gap-2">
            <span className="hidden text-[11px] text-muted-foreground md:inline">
              Enter to start · Shift + Enter for a new line
            </span>
            <Button
              type="submit"
              size="sm"
              disabled={pending}
              data-testid="ask-submit"
              className="group"
            >
              {pending ? (
                <LoaderCircle className="animate-spin" data-motion="loop" aria-hidden />
              ) : (
                <Search aria-hidden />
              )}
              {pending ? 'Starting' : 'Research'}
              {/*
               * The arrow takes no width until it is wanted. It used to render
               * at `opacity: 0`, which reserved its box and left a gap after
               * the label that read as a mistake; collapsing the wrapper to
               * zero width instead means the button is exactly as wide as its
               * label and grows when the arrow arrives. Width on a 14px element
               * is cheap, and the growth is the point of the interaction.
               */}
              {pending ? null : (
                <span
                  className={cn(
                    'inline-flex w-0 justify-end overflow-hidden opacity-0',
                    'transition-[width,opacity] duration-[var(--duration-base)] ease-[var(--ease-out-quick)]',
                    'group-hover:w-4 group-hover:opacity-100',
                    'group-focus-visible:w-4 group-focus-visible:opacity-100',
                  )}
                  aria-hidden
                >
                  <ArrowRight className="size-3.5 shrink-0" />
                </span>
              )}
            </Button>
          </div>
        </div>

        {error ? (
          <p id={errorId} role="alert" className="reveal px-3 pb-2 text-xs text-destructive-strong">
            {error}
          </p>
        ) : null}
      </form>

      {/*
       * A suggestion fills the box rather than navigating: the point of the
       * question box is that starting a run never takes you off the page you
       * are typing on, and a starter question you cannot then edit is a
       * template, not a suggestion.
       */}
      {suggestions && suggestions.length > 0 ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground">Try:</span>
          {suggestions.map((item) => (
            <Button
              key={item.label}
              type="button"
              variant="outline"
              size="sm"
              className="group rounded-full text-xs"
              onClick={() => {
                setQuestion(item.question);
                setError(null);
                document.getElementById('ask')?.focus();
              }}
            >
              {item.label}
              <ArrowUpRight
                className="size-3 opacity-60 transition-transform duration-[var(--duration-base)] group-hover:-translate-y-0.5 group-hover:translate-x-0.5"
                aria-hidden
              />
            </Button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
