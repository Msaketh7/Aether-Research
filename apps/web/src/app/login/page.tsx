'use client';

import { Mail, Sparkles } from 'lucide-react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useRef, useState, type FormEvent } from 'react';
import { AuthCard, AuthSubmit } from '@/components/auth/auth-card';
import { AuthField, PasswordField } from '@/components/auth/auth-field';
import { AuthShell } from '@/components/auth/auth-shell';
import { ConfirmationBanner, ResendButton } from '@/components/auth/confirmation';
import { SsoButtons, SsoButtonsSkeleton } from '@/components/auth/sso-buttons';
import { SsoError } from '@/components/auth/sso-error';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { ApiError } from '@/lib/api/client';
import { API_MODE } from '@/lib/api/config';
import { useLogin, useSsoOptions } from '@/lib/api/queries';
import { safeDestination } from '@/lib/auth/redirect';
import { shake } from '@/lib/motion';

/**
 * Sign-in (FR-1).
 *
 * Two credential paths reach the same place. The password form posts to the
 * API, which verifies an Argon2id hash and mints a first-party token. The SSO
 * buttons hand off to Auth0 or Supabase, which broker Google or GitHub and
 * come back through the API's callback. Either way the browser ends up
 * holding the same `HttpOnly` cookie pair and the app above this page cannot
 * tell which was used.
 *
 * The form is in the first HTML. `useSearchParams` would put it behind a
 * Suspense boundary, and a prerendered page ships that boundary's fallback
 * and swaps the real form in after hydration - a skeleton flash and a layout
 * shift on the one page everybody sees, which it did until this was changed.
 * Only two things need `?next=`: the redirect after a password sign-in, which
 * reads it when it happens, and the SSO links, which carry it in their `href`
 * and are client-rendered anyway because they wait on a query.
 */
export default function LoginPage() {
  return (
    <AuthShell>
      <AuthCard title="Welcome back" description="Sign in to pick up where your research left off.">
        <SignInForm />
      </AuthCard>
    </AuthShell>
  );
}

/** Where to go once signed in, read at the moment it is needed. */
function currentDestination() {
  return safeDestination(new URLSearchParams(window.location.search).get('next'));
}

function SsoForDestination() {
  const params = useSearchParams();
  return <SsoButtons next={safeDestination(params.get('next'))} />;
}

function SignInForm() {
  const router = useRouter();
  const login = useLogin();
  const sso = useSsoOptions();
  const form = useRef<HTMLFormElement>(null);

  const [email, setEmail] = useState(API_MODE === 'mock' ? 'analyst@aether.dev' : '');
  const [password, setPassword] = useState(API_MODE === 'mock' ? 'demo-password' : '');

  const error = login.error instanceof ApiError ? login.error : null;
  const fieldErrors = error?.details ?? {};
  // The one refusal with something to do about it: the password was right, the
  // address just has not been confirmed. Offered only after a correct password,
  // so it confirms nothing the caller had not already proved.
  const unconfirmed = error?.code === 'email_not_confirmed';

  // Default to showing the form. While the options are loading, or if that
  // request failed, a page with no way to sign in at all is worse than one
  // offering a method the deployment happens to have turned off.
  const passwordEnabled = sso.data?.password_enabled ?? true;
  const registrationEnabled = sso.data?.registration_enabled ?? true;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    login.mutate(
      { email, password },
      {
        onSuccess: () => router.push(currentDestination()),
        // The card, not the form: the whole surface refusing reads as "no",
        // where a shaking form reads as a broken layout.
        onError: () => shake(form.current?.closest<HTMLElement>('[data-auth-card]') ?? null),
      },
    );
  };

  return (
    <div className="stagger flex flex-col gap-5">
      <SsoError />
      <ConfirmationBanner />

      {passwordEnabled ? (
        <form ref={form} onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
          <AuthField
            id="email"
            name="email"
            type="email"
            label="Email"
            icon={Mail}
            autoComplete="email"
            placeholder="you@company.com"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            error={fieldErrors.email}
          />

          <PasswordField
            id="password"
            name="password"
            label="Password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            error={fieldErrors.password}
          />

          {error && unconfirmed ? (
            <div className="reveal flex flex-col gap-3" data-testid="email-not-confirmed">
              <Alert variant="warning">
                <AlertDescription className="text-foreground">{error.message}</AlertDescription>
              </Alert>
              <ResendButton email={email} />
            </div>
          ) : error && Object.keys(fieldErrors).length === 0 ? (
            <Alert variant="danger">
              <AlertDescription>{error.message}</AlertDescription>
            </Alert>
          ) : null}

          <AuthSubmit
            pending={login.isPending}
            done={login.isSuccess}
            pendingLabel="Signing in…"
            doneLabel="Signed in"
            data-testid="login-submit"
            className="mt-1"
          >
            Sign in
          </AuthSubmit>
        </form>
      ) : null}

      <Suspense fallback={<SsoButtonsSkeleton />}>
        <SsoForDestination />
      </Suspense>

      {passwordEnabled && registrationEnabled ? (
        <p className="text-center text-sm text-muted-foreground">
          No account yet?{' '}
          <Link
            href="/register"
            className="underline-grow font-medium text-primary-strong transition-colors hover:text-foreground"
          >
            Create one
          </Link>
        </p>
      ) : null}

      {API_MODE === 'mock' ? (
        <p className="flex items-start gap-2 rounded-lg border border-border/60 bg-background/40 px-3 py-2.5 text-xs leading-relaxed text-muted-foreground">
          <Sparkles className="mt-px size-3.5 shrink-0 text-primary-strong" aria-hidden />
          <span>
            Demo build: credentials are pre-filled and any valid-looking pair is accepted. The live
            API checks them for real.
          </span>
        </p>
      ) : null}
    </div>
  );
}
