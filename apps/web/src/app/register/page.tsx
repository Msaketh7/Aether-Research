'use client';

import { Mail, UserRound } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useRef, useState, type FormEvent } from 'react';
import { AuthCard, AuthSubmit } from '@/components/auth/auth-card';
import { AuthField, PasswordField } from '@/components/auth/auth-field';
import { AuthShell } from '@/components/auth/auth-shell';
import { CheckInbox } from '@/components/auth/confirmation';
import { SsoButtons } from '@/components/auth/sso-buttons';
import { SsoError } from '@/components/auth/sso-error';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { ApiError } from '@/lib/api/client';
import { useRegister } from '@/lib/api/queries';
import { DEFAULT_DESTINATION } from '@/lib/auth/redirect';
import { shake } from '@/lib/motion';
import { cn } from '@/lib/utils';

/** The API's floor (`MIN_PASSWORD_LENGTH`). Stated so the requirement is
 * visible before the form is submitted rather than only in a refusal. The
 * server enforces it; this is a courtesy, not a check. */
const MIN_PASSWORD_LENGTH = 12;

/**
 * Create an account (FR-1).
 *
 * When registration signs the new account in, this lands on the question box
 * rather than sending the person to the sign-in form they just filled out.
 * When Supabase holds the passwords (ADR 0025) it usually cannot: the address
 * has to be confirmed first, so the card turns into "check your inbox", and
 * the emailed link brings the person back to sign in.
 *
 * Every refusal is rendered from the `details` map in the error envelope, which
 * is where the password policy arrives: the rules live on the server, and
 * restating them here as validation would mean two policies to keep in step.
 * The length bar under the password is the same courtesy as the sentence
 * beside it - it never blocks a submit.
 *
 * The form's state lives in `RegisterForm`, below the shell, so a keystroke
 * re-renders the form and not the video, the story column and the ticker
 * around it.
 */
export default function RegisterPage() {
  return (
    <AuthShell>
      <AuthCard
        title="Create your account"
        description="Start asking questions that come back with their evidence attached."
      >
        <RegisterForm />
      </AuthCard>
    </AuthShell>
  );
}

function RegisterForm() {
  const router = useRouter();
  const register = useRegister();
  const form = useRef<HTMLFormElement>(null);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');

  const error = register.error instanceof ApiError ? register.error : null;
  const fieldErrors = error?.details ?? {};
  const waiting = register.data && 'confirmation_required' in register.data ? register.data : null;

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    register.mutate(
      { email, password, name },
      {
        onSuccess: (data) => {
          if ('user' in data) router.push(DEFAULT_DESTINATION);
        },
        onError: () => shake(form.current?.closest<HTMLElement>('[data-auth-card]') ?? null),
      },
    );
  };

  if (waiting) {
    return (
      <CheckInbox
        email={waiting.email}
        onStartAgain={() => {
          register.reset();
          setPassword('');
        }}
      />
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <SsoError />

      <form ref={form} onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
        <AuthField
          id="name"
          name="name"
          label="Name"
          icon={UserRound}
          autoComplete="name"
          placeholder="Ada Lovelace"
          value={name}
          onChange={(event) => setName(event.target.value)}
          error={fieldErrors.name}
        />

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
          autoComplete="new-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          error={fieldErrors.password}
          help={
            <div className="flex flex-col gap-2">
              <LengthBar length={password.length} />
              <span>
                At least {MIN_PASSWORD_LENGTH} characters. Length is what protects it; a long phrase
                beats a short puzzle.
              </span>
            </div>
          }
        />

        {error && Object.keys(fieldErrors).length === 0 ? (
          <Alert variant="danger">
            <AlertDescription>{error.message}</AlertDescription>
          </Alert>
        ) : null}

        <AuthSubmit
          pending={register.isPending}
          done={register.isSuccess}
          pendingLabel="Creating account…"
          doneLabel="Account created"
          data-testid="register-submit"
          className="mt-1"
        >
          Create account
        </AuthSubmit>
      </form>

      {/* Below the form, matching sign-in. "Continue with" still creates
          the account on first use; it is the alternative, not the
          default, and the two pages must not disagree about that. */}
      <SsoButtons />

      <p className="text-center text-sm text-muted-foreground">
        Already have an account?{' '}
        <Link
          href="/login"
          className="underline-grow font-medium text-primary-strong transition-colors hover:text-foreground"
        >
          Sign in
        </Link>
      </p>
    </div>
  );
}

/**
 * How far the password is toward the stated floor. A scaled bar rather than a
 * width so it never reflows the help text under it, and green only once the
 * floor is reached - colour here confirms the sentence, it does not replace it.
 */
function LengthBar({ length }: { length: number }) {
  const ratio = Math.min(length / MIN_PASSWORD_LENGTH, 1);
  return (
    <span aria-hidden className="block h-1 overflow-hidden rounded-full bg-foreground/10">
      <span
        className={cn(
          'block h-full origin-left rounded-full',
          'transition-[transform,background-color] duration-[var(--duration-base)] ease-[var(--ease-out-soft)]',
          ratio === 1 ? 'bg-success' : 'bg-primary',
        )}
        style={{ transform: `scaleX(${ratio})` }}
      />
    </span>
  );
}
