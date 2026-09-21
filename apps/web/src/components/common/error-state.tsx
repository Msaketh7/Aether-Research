'use client';

import { AlertTriangle, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api/client';

/** Renders a failed query without leaking internals to the user. */
export function ErrorState({
  error,
  onRetry,
  title = 'Something went wrong',
}: {
  error: unknown;
  onRetry?: () => void;
  title?: string;
}) {
  const apiError = error instanceof ApiError ? error : null;
  const message =
    apiError?.message ?? (error instanceof Error ? error.message : 'An unexpected error occurred.');

  return (
    <div
      role="alert"
      className="reveal flex flex-col items-center justify-center gap-2 rounded-xl border border-destructive/30 bg-destructive/5 px-6 py-12 text-center"
    >
      <span className="mb-1 flex size-11 items-center justify-center rounded-full bg-destructive/12 ring-1 ring-destructive/25">
        <AlertTriangle className="size-5 text-destructive-strong" aria-hidden />
      </span>
      <p className="text-sm font-medium">{title}</p>
      <p className="max-w-md text-sm text-muted-foreground">{message}</p>
      {apiError?.traceId ? (
        <p className="font-mono text-[11px] text-muted-foreground">trace {apiError.traceId}</p>
      ) : null}
      {onRetry ? (
        <Button variant="outline" size="sm" className="group mt-3" onClick={onRetry}>
          <RefreshCw className="group-hover:-rotate-180" aria-hidden />
          Try again
        </Button>
      ) : null}
    </div>
  );
}
