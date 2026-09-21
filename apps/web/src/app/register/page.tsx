'use client';

import { LoaderCircle } from 'lucide-react';
import { AetherMark } from '@/components/layout/brand';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState, type FormEvent } from 'react';
import { SsoButtons } from '@/components/auth/sso-buttons';
import { DEFAULT_DESTINATION } from '@/lib/auth/redirect';
import { SsoError } from '@/components/auth/sso-error';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ApiError } from '@/lib/api/client';
import { APP_NAME } from '@/lib/api/config';
import { useRegister } from '@/lib/api/queries';

/** The API's floor (`MIN_PASSWORD_LENGTH`). Stated so the requirement is
 * visible before the form is submitted rather than only in a refusal. The
 * server enforces it; this is a courtesy, not a check. */
const MIN_PASSWORD_LENGTH = 12;

/**
 * Create an account (FR-1).
 *
 * Registration signs the new account in, so this lands on the dashboard rather
 * than sending the person to the sign-in form they just filled out.
 *
 * Every refusal is rendered from the `details` map in the error envelope, which
 * is where the password policy arrives: the rules live on the server, and
 * restating them here as validation would mean two policies to keep in step.
 */
export default function RegisterPage() {
  const router = useRouter();
  const register = useRegister();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');

  const error = register.error instanceof ApiError ? register.error : null;
  const fieldErrors = error?.details ?? {};

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    register.mutate(
      { email, password, name },
      { onSuccess: () => router.push(DEFAULT_DESTINATION) },
    );
  };

  return (
    <main className="relative flex min-h-dvh items-center justify-center px-4 py-12">
      <div className="mesh pointer-events-none absolute inset-0" aria-hidden />
      <div className="relative w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-2 text-center">
          <AetherMark className="size-8" />
          <h1 className="text-xl font-semibold tracking-tight">{APP_NAME}</h1>
          <p className="text-sm text-muted-foreground">Create an account to start researching.</p>
        </div>

        <SsoError />

        <Card className="shadow-e2">
          <CardContent className="flex flex-col gap-4 pt-5">
            <form onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="name">Name</Label>
                <Input
                  id="name"
                  name="name"
                  autoComplete="name"
                  value={name}
                  onChange={(event) => setName(event.target.value)}
                  aria-invalid={Boolean(fieldErrors.name)}
                />
              </div>

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
                  autoComplete="new-password"
                  required
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  aria-invalid={Boolean(fieldErrors.password)}
                  aria-describedby="password-help"
                />
                <p
                  id="password-help"
                  className={
                    fieldErrors.password
                      ? 'text-xs text-destructive-strong'
                      : 'text-xs text-muted-foreground'
                  }
                >
                  {fieldErrors.password
                    ? fieldErrors.password.join(' ')
                    : `At least ${MIN_PASSWORD_LENGTH} characters. Length is what protects it; a long phrase beats a short puzzle.`}
                </p>
              </div>

              {error && Object.keys(fieldErrors).length === 0 ? (
                <Alert variant="danger">
                  <AlertDescription>{error.message}</AlertDescription>
                </Alert>
              ) : null}

              <Button type="submit" disabled={register.isPending} data-testid="register-submit">
                {register.isPending ? <LoaderCircle className="animate-spin" aria-hidden /> : null}
                Create account
              </Button>
            </form>

            {/* Below the form, matching sign-in. "Continue with" still creates
                the account on first use; it is the alternative, not the
                default, and the two pages must not disagree about that. */}
            <SsoButtons />
          </CardContent>
        </Card>

        <p className="mt-4 text-center text-xs text-muted-foreground">
          Already have an account?{' '}
          <Link href="/login" className="underline underline-offset-4">
            Sign in
          </Link>
        </p>
      </div>
    </main>
  );
}
