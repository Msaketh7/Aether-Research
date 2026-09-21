'use client';

import type { SsoFailure } from '@aether/shared-types';
import { useSearchParams } from 'next/navigation';
import { Suspense } from 'react';
import { Alert, AlertDescription } from '@/components/ui/alert';

/**
 * Why a sign-in attempt came back without a session.
 *
 * **The code is looked up, never rendered.** It arrives in the address bar,
 * which means an attacker can put anything there and send somebody the link.
 * Rendering it would be a reflected-content injection - at best a confusing
 * message, at worst "Your account was locked. Call this number." on a page
 * that is unmistakably ours. Matching against this table and falling back to a
 * generic string is what makes that impossible rather than merely unlikely.
 *
 * The wording also never says whether an address is registered: `/login` is
 * reachable by anybody, and these messages are subject to the same
 * enumeration rule as the credential endpoint's single refusal.
 */
const MESSAGES: Record<SsoFailure, string> = {
  access_denied: 'Sign-in was cancelled. You can try again or use your password.',
  invalid_state: 'That sign-in link expired or was already used. Start again from this page.',
  exchange_failed: 'We could not complete sign-in with that provider. Please try again.',
  email_unverified:
    'That provider has not verified the email address on the account. Verify it there, then try again.',
  account_conflict:
    'That email is already registered with a different sign-in method. Use the original method, then link this one from Settings.',
  provider_unavailable: 'That sign-in provider is unavailable right now. Please try again shortly.',
  registration_closed: 'This deployment is not accepting new accounts.',
  unknown: 'Sign-in did not complete. Please try again.',
};

function isFailure(value: string | null): value is SsoFailure {
  return value !== null && Object.hasOwn(MESSAGES, value);
}

function SsoErrorBanner() {
  const params = useSearchParams();
  const raw = params.get('error');
  if (raw === null) return null;

  const message = isFailure(raw) ? MESSAGES[raw] : MESSAGES.unknown;

  return (
    <Alert variant="danger" className="mb-4" data-testid="sso-error">
      <AlertDescription>{message}</AlertDescription>
    </Alert>
  );
}

/**
 * Wrapped here rather than at each call site.
 *
 * `useSearchParams` opts a route out of static rendering unless it sits under
 * a Suspense boundary, and this component is dropped onto pages that are
 * otherwise static. Owning the boundary means a page cannot forget it, and the
 * fallback is `null` because a banner that is usually absent has nothing
 * useful to show while it decides.
 */
export function SsoError() {
  return (
    <Suspense fallback={null}>
      <SsoErrorBanner />
    </Suspense>
  );
}
