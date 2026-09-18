'use client';

import { useRouter } from 'next/navigation';
import { useEffect, type ReactNode } from 'react';
import { Skeleton } from '@/components/ui/skeleton';
import { ApiError } from '@/lib/api/client';
import { useCurrentUser } from '@/lib/api/queries';

/**
 * Keeps signed-out visitors out of the application chrome (FR-1).
 *
 * The check is `/auth/me`, and it is a *client* check on purpose. The session
 * lives in a cookie scoped to the API, which in a real deployment is a
 * different host from this one - so Next.js middleware here cannot read it,
 * and asking it to would mean either proxying every request through this
 * server or duplicating session validation in two languages.
 *
 * That makes this a redirect, not a security boundary. The boundary is the
 * API: every endpoint resolves the session itself and returns 401 without one,
 * so a person who skips this component sees an application shell with nothing
 * in it rather than somebody else's research. What this adds is that they are
 * sent somewhere useful instead.
 *
 * Only an authentication failure redirects. An API that is down or broken
 * leaves the pages mounted so each renders its own error state, which says
 * what happened - being bounced to a sign-in form by a 503 is the least
 * informative possible response to an outage.
 */
export function RequireSession({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { isPending, isError, error } = useCurrentUser();
  const signedOut = isError && error instanceof ApiError && error.isAuthError;

  useEffect(() => {
    if (signedOut) router.replace('/login');
  }, [signedOut, router]);

  if (isPending) {
    return (
      <div className="flex flex-col gap-4 p-6" aria-busy="true" aria-label="Checking your session">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-40" />
      </div>
    );
  }

  // Rendering nothing for the instant before the redirect lands, rather than
  // flashing a dashboard the API will refuse to fill.
  if (signedOut) return null;

  return <>{children}</>;
}
