'use client';

import { LoaderCircle } from 'lucide-react';
import { AetherMark } from '@/components/layout/brand';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useState, type FormEvent } from 'react';
import { SsoButtons } from '@/components/auth/sso-buttons';
import { SsoError } from '@/components/auth/sso-error';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Skeleton } from '@/components/ui/skeleton';
import { ApiError } from '@/lib/api/client';
import { API_MODE, APP_NAME } from '@/lib/api/config';
import { useLogin, useSsoOptions } from '@/lib/api/queries';
import { safeDestination } from '@/lib/auth/redirect';

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
 * `useSearchParams` needs a Suspense boundary: without one Next cannot
 * statically render the shell, and the whole route is forced dynamic.
 */
export default function LoginPage() {
  return (
    <main className="relative flex min-h-dvh items-center justify-center px-4 py-12">
      <div className="mesh pointer-events-none absolute inset-0" aria-hidden />
      <div className="relative w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-2 text-center">
          <AetherMark className="size-8" />
          <h1 className="text-xl font-semibold tracking-tight">{APP_NAME}</h1>
          <p className="text-sm text-muted-foreground">
            Autonomous research with traceable evidence.
          </p>
        </div>

        <Suspense fallback={<Skeleton className="h-64 w-full" />}>
          <SignInCard />
        </Suspense>
      </div>
    </main>
  );
}

function SignInCard() {
  const router = useRouter();
  const params = useSearchParams();
  const login = useLogin();
  const sso = useSsoOptions();

  const destination = safeDestination(params.get('next'));
  const [email, setEmail] = useState(API_MODE === 'mock' ? 'analyst@aether.dev' : '');
  const [password, setPassword] = useState(API_MODE === 'mock' ? 'demo-password' : '');

  const error = login.error instanceof ApiError ? login.error : null;
  const fieldErrors = error?.details ?? {};

  // Default to showing the form. While the options are loading, or if that
  // request failed, a page with no way to sign in at all is worse than one
  // offering a method the deployment happens to have turned off.
  const passwordEnabled = sso.data?.password_enabled ?? true;
  const registrationEnabled = sso.data?.registration_enabled ?? true;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    login.mutate({ email, password }, { onSuccess: () => router.push(destination) });
  };

  return (
    <>
      <SsoError />

      <Card>
        <CardContent className="flex flex-col gap-4 pt-5">
          {passwordEnabled ? (
            <form onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  name="email"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  aria-invalid={Boolean(fieldErrors.email)}
                  aria-describedby={fieldErrors.email ? 'email-error' : undefined}
                />
                {fieldErrors.email ? (
                  <p id="email-error" className="text-xs text-destructive-strong">
                    {fieldErrors.email.join(' ')}
                  </p>
                ) : null}
              </div>

              <div className="flex flex-col gap-1.5">
                <Label htmlFor="password">Password</Label>
                <Input
                  id="password"
                  name="password"
                  type="password"
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  aria-invalid={Boolean(fieldErrors.password)}
                  aria-describedby={fieldErrors.password ? 'password-error' : undefined}
                />
                {fieldErrors.password ? (
                  <p id="password-error" className="text-xs text-destructive-strong">
                    {fieldErrors.password.join(' ')}
                  </p>
                ) : null}
              </div>

              {error && Object.keys(fieldErrors).length === 0 ? (
                <Alert variant="danger">
                  <AlertDescription>{error.message}</AlertDescription>
                </Alert>
              ) : null}

              <Button type="submit" disabled={login.isPending} data-testid="login-submit">
                {login.isPending ? <LoaderCircle className="animate-spin" aria-hidden /> : null}
                Sign in
              </Button>
            </form>
          ) : null}

          <SsoButtons next={destination} />
        </CardContent>
      </Card>

      {passwordEnabled && registrationEnabled ? (
        <p className="mt-4 text-center text-xs text-muted-foreground">
          No account yet?{' '}
          <Link href="/register" className="underline underline-offset-4">
            Create one
          </Link>
        </p>
      ) : null}

      {API_MODE === 'mock' ? (
        <p className="mt-2 text-center text-xs text-muted-foreground">
          Demo build: credentials are pre-filled and any valid-looking pair is accepted. The live
          API checks them for real.
        </p>
      ) : null}
    </>
  );
}
