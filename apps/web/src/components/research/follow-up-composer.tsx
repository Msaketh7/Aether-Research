'use client';

import { ArrowUp, LoaderCircle } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useId, useState, type FormEvent, type KeyboardEvent } from 'react';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { ApiError } from '@/lib/api/client';
import { useFollowUpResearch } from '@/lib/api/queries';
import { MIN_QUESTION_LENGTH, MAX_QUESTION_LENGTH } from '@/lib/research/new-research-schema';
import { cn } from '@/lib/utils';

/**
 * The box under the answer.
 *
 * A follow-up is a *new run* that names this one as its parent, not a second
 * message on a socket - the reply takes minutes and survives the tab being
 * closed, so it has to be a durable job with an id of its own. What makes it
 * read as a conversation is that asking never leaves the page you were reading:
 * you land on the new run with its answer already arriving.
 *
 * Disabled while the run is still working, with a reason. A follow-up to an
 * answer that does not exist yet is a question about nothing, and the run it
 * would fork from has no findings to carry forward.
 */
export function FollowUpComposer({
  runId,
  disabled = false,
  disabledReason,
  className,
}: {
  runId: string;
  disabled?: boolean;
  disabledReason?: string;
  className?: string;
}) {
  const router = useRouter();
  const followUp = useFollowUpResearch(runId);
  const [question, setQuestion] = useState('');
  const [error, setError] = useState<string | null>(null);
  const errorId = useId();
  const hintId = useId();

  const pending = followUp.isPending;
  const blocked = disabled || pending;

  const submit = () => {
    const trimmed = question.trim();
    if (trimmed.length < MIN_QUESTION_LENGTH) {
      setError(`A follow-up needs at least ${MIN_QUESTION_LENGTH} characters.`);
      return;
    }
    if (trimmed.length > MAX_QUESTION_LENGTH) {
      setError(`A follow-up may be at most ${MAX_QUESTION_LENGTH} characters.`);
      return;
    }

    setError(null);
    followUp.mutate(trimmed, {
      onSuccess: (response) => {
        setQuestion('');
        router.push(`/research/${response.run_id}`);
      },
      onError: (err) =>
        setError(
          err instanceof ApiError ? err.message : 'Could not start the follow-up. Try again.',
        ),
    });
  };

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    submit();
  };

  // Enter sends, Shift+Enter breaks the line - the same way round as the
  // question box on the home screen, and as every chat assistant.
  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (!blocked) submit();
    }
  };

  return (
    <form
      onSubmit={onSubmit}
      noValidate
      data-testid="follow-up-composer"
      className={cn(
        'rounded-2xl border border-border bg-card/80 p-2 shadow-e2 backdrop-blur-sm',
        'transition-[border-color,box-shadow] duration-[var(--duration-base)] ease-[var(--ease-out-soft)]',
        'focus-within:border-primary/45 focus-within:shadow-glow',
        className,
      )}
    >
      <label htmlFor={`follow-up-${runId}`} className="sr-only">
        Ask a follow-up question
      </label>
      <div className="flex items-end gap-2">
        <Textarea
          id={`follow-up-${runId}`}
          name="question"
          rows={1}
          value={question}
          onChange={(event) => {
            setQuestion(event.target.value);
            if (error) setError(null);
          }}
          onKeyDown={onKeyDown}
          disabled={blocked}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? errorId : disabled ? hintId : undefined}
          data-testid="follow-up-input"
          placeholder={
            disabled ? (disabledReason ?? 'Available once this run finishes') : 'Ask a follow-up…'
          }
          className="min-h-11 resize-none border-0 bg-transparent px-3 py-2.5 text-base shadow-none focus-visible:shadow-none sm:text-sm"
        />
        <Button
          type="submit"
          size="icon"
          disabled={blocked || question.trim().length === 0}
          data-testid="follow-up-submit"
          aria-label="Ask this follow-up"
          className="mb-1 shrink-0 rounded-full"
        >
          {pending ? (
            <LoaderCircle className="animate-spin" data-motion="loop" aria-hidden />
          ) : (
            <ArrowUp aria-hidden />
          )}
        </Button>
      </div>

      {error ? (
        <p id={errorId} role="alert" className="reveal px-3 pb-1 text-xs text-destructive-strong">
          {error}
        </p>
      ) : disabled && disabledReason ? (
        <p id={hintId} className="px-3 pb-1 text-xs text-muted-foreground">
          {disabledReason}
        </p>
      ) : null}
    </form>
  );
}
