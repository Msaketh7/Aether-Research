'use client';

import { FlaskConical, LoaderCircle } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useState, type FormEvent } from 'react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { ApiError } from '@/lib/api/client';
import { API_MODE, APP_NAME } from '@/lib/api/config';
import { useLogin } from '@/lib/api/queries';

/**
 * Sign-in (FR-1).
 *
 * The form posts to the API, renders field-level validation from the `details`
 * map in the error envelope, and handles the failure paths. Against the live
 * API (Phase 20) the credential check is real: an Argon2id verification and an
 * `HttpOnly` session cookie. In mock mode any well-formed pair is accepted.
 */
export default function LoginPage() {
  const router = useRouter();
  const login = useLogin();
  const [email, setEmail] = useState(API_MODE === 'mock' ? 'analyst@aether.dev' : '');
  const [password, setPassword] = useState(API_MODE === 'mock' ? 'demo-password' : '');

  const error = login.error instanceof ApiError ? login.error : null;
  const fieldErrors = error?.details ?? {};

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    login.mutate({ email, password }, { onSuccess: () => router.push('/dashboard') });
  };

  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-2 text-center">
          <FlaskConical className="size-6 text-primary" aria-hidden />
          <h1 className="text-lg font-semibold tracking-tight">{APP_NAME}</h1>
          <p className="text-sm text-muted-foreground">
            Autonomous research with traceable evidence.
          </p>
        </div>

        <Card>
          <CardContent className="pt-5">
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
                  <p id="email-error" className="text-xs text-destructive">
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
                  <p id="password-error" className="text-xs text-destructive">
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
          </CardContent>
        </Card>

        <p className="mt-4 text-center text-xs text-muted-foreground">
          No account yet?{' '}
          <Link href="/register" className="underline underline-offset-4">
            Create one
          </Link>
        </p>

        {API_MODE === 'mock' ? (
          <p className="mt-2 text-center text-xs text-muted-foreground">
            Demo build: credentials are pre-filled and any valid-looking pair is accepted. The live
            API checks them for real.
          </p>
        ) : null}
      </div>
    </main>
  );
}
