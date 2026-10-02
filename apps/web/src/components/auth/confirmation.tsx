'use client';

import { ArrowLeft, CircleCheck, MailCheck, RotateCw, TriangleAlert } from 'lucide-react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useState } from 'react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api/client';
import { useResendConfirmation } from '@/lib/api/queries';

/** Long enough that a double click is one email, short enough not to strand anyone. */
const RESEND_COOLDOWN_SECONDS = 30;

/**
 * What a confirmation link can come back with, in our words.
 *
 * Supabase reports a failed link in the URL *fragment* -
 * `#error_code=otp_expired&error_description=...`. The fragment is
 * attacker-writable (anyone can send somebody a link to `/login#...`), so the
 * code is looked up here and the description is never rendered - the same rule
 * `SsoError` applies to the query string.
 */
const LINK_FAILURES: Record<string, string> = {
  otp_expired: 'That confirmation link has expired or was already used. Sign in to get a new one.',
  access_denied: 'That confirmation link could not be used. Sign in to get a new one.',
};
const LINK_FAILURE_FALLBACK = 'That confirmation link did not work. Sign in to get a new one.';

type LinkOutcome = { kind: 'confirmed' } | { kind: 'failed'; message: string } | null;

/**
 * The sign-in page's word on a confirmation link the person just followed.
 *
 * Supabase confirms the address *before* redirecting here, and on success it
 * also appends a Supabase session to the fragment. This system never uses
 * that session - it issues its own at sign-in - so the fragment is scrubbed
 * from the address bar and history immediately: a bearer token left in a URL
 * ends up in screenshots, bookmarks and shared links.
 */
export function ConfirmationBanner() {
  return (
    <Suspense fallback={null}>
      <ConfirmationBannerInner />
    </Suspense>
  );
}

function ConfirmationBannerInner() {
  const params = useSearchParams();
  const [outcome, setOutcome] = useState<LinkOutcome>(null);

  useEffect(() => {
    const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ''));
    const code = fragment.get('error_code') ?? fragment.get('error');
    const confirmed = params.get('confirmed') === '1';

    if (window.location.hash) {
      const clean = `${window.location.pathname}${window.location.search}`;
      window.history.replaceState(window.history.state, '', clean);
    }

    // Read once, from what the browser arrived with. An effect rather than a
    // render-time read because the fragment never reaches the server, and a
    // banner that differs between server and client HTML is a hydration error.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setOutcome(
      code
        ? { kind: 'failed', message: LINK_FAILURES[code] ?? LINK_FAILURE_FALLBACK }
        : confirmed
          ? { kind: 'confirmed' }
          : null,
    );
  }, [params]);

  if (outcome === null) return null;

  if (outcome.kind === 'failed') {
    return (
      <Alert variant="warning" data-testid="confirmation-failed">
        <TriangleAlert aria-hidden />
        <AlertDescription className="text-foreground">{outcome.message}</AlertDescription>
      </Alert>
    );
  }

  return (
    <Alert variant="info" data-testid="confirmation-succeeded">
      <CircleCheck aria-hidden />
      <AlertDescription className="text-foreground">
        Email confirmed. Sign in to continue.
      </AlertDescription>
    </Alert>
  );
}

/**
 * Sends the sign-up link again, then holds off for a moment.
 *
 * The API answers the same way for any address, so "Sent" here means "asked",
 * which is the honest thing for it to mean. The cooldown is a courtesy to the
 * mailer's hourly limit, not a control - the server's per-address bucket is.
 */
export function ResendButton({ email, className }: { email: string; className?: string }) {
  const resend = useResendConfirmation();
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    if (cooldown <= 0) return;
    const timer = window.setTimeout(() => setCooldown((left) => left - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [cooldown]);

  const error = resend.error instanceof ApiError ? resend.error : null;
  const sent = resend.isSuccess && cooldown > 0;

  return (
    <div className={className}>
      <Button
        type="button"
        variant="outline"
        className="h-10 w-full"
        disabled={resend.isPending || cooldown > 0 || !email}
        onClick={() =>
          resend.mutate({ email }, { onSuccess: () => setCooldown(RESEND_COOLDOWN_SECONDS) })
        }
        data-testid="resend-confirmation"
      >
        <RotateCw className={resend.isPending ? 'animate-spin' : undefined} aria-hidden />
        {sent ? `Sent. Resend again in ${cooldown}s` : 'Resend confirmation email'}
      </Button>
      {/* Polite: the person is still on the page and does not need interrupting. */}
      <p className="mt-2 min-h-4 text-center text-xs text-muted-foreground" aria-live="polite">
        {error ? error.message : sent ? 'Check your inbox, and your spam folder.' : null}
      </p>
    </div>
  );
}

/**
 * What the registration card shows once Supabase has emailed a link.
 *
 * It says where the link went, what to do with it, and what to do if it does
 * not arrive - the three questions everyone asks at this point. "Use a
 * different address" starts the form again rather than editing in place,
 * because the address is the one thing that has to be retyped carefully.
 */
export function CheckInbox({ email, onStartAgain }: { email: string; onStartAgain: () => void }) {
  return (
    <div className="reveal flex flex-col gap-5" data-testid="check-inbox">
      <div className="flex flex-col items-center gap-3 text-center">
        <span className="auth-feature-icon auth-pop size-12 rounded-2xl">
          <MailCheck className="size-6" aria-hidden />
        </span>
        <h2 className="font-display text-xl font-medium text-foreground">Check your inbox</h2>
        <p className="text-sm leading-relaxed text-muted-foreground">
          We sent a confirmation link to{' '}
          <span className="font-medium [overflow-wrap:anywhere] text-foreground">{email}</span>.
          Follow it, then sign in.
        </p>
      </div>

      <Link href="/login" className="auth-cta group gap-2">
        <ArrowLeft
          aria-hidden
          className="relative z-[1] size-4 transition-transform duration-[var(--duration-base)] ease-[var(--ease-spring)] group-hover:-translate-x-1"
        />
        <span className="relative z-[1]">Go to sign in</span>
      </Link>

      <ResendButton email={email} />

      <button
        type="button"
        onClick={onStartAgain}
        className="underline-grow mx-auto text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        Use a different address
      </button>
    </div>
  );
}
