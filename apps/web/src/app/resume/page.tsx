'use client';

import { LoaderCircle } from 'lucide-react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useRef, useState } from 'react';
import { AetherMark } from '@/components/layout/brand';
import { Button } from '@/components/ui/button';
import { refreshSession } from '@/lib/auth/refresh';
import { safeDestination } from '@/lib/auth/redirect';

/**
 * Where a guarded page sends a visitor whose access token is gone.
 *
 * The middleware cannot tell a signed-out visitor from one whose fifteen-minute
 * access token simply expired: the refresh token that would renew it is in a
 * cookie scoped to the API's refresh path, which a page request never carries.
 * So this page asks the API. A renewal sends the visitor on to where they were
 * going, as though nothing happened; a refusal sends them to sign in, with the
 * same destination carried along. Only an API that cannot be reached stops
 * here, because that is not an answer about the session and sending someone to
 * a sign-in form over an outage tells them the wrong thing.
 *
 * `useSearchParams` needs a Suspense boundary, as on the sign-in page.
 */
export default function ResumePage() {
  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-12">
      <div className="flex w-full max-w-sm flex-col items-center gap-4 text-center">
        <AetherMark className="size-8" />
        <Suspense fallback={<Checking />}>
          <Resume />
        </Suspense>
      </div>
    </main>
  );
}

function Resume() {
  const router = useRouter();
  const params = useSearchParams();
  const destination = safeDestination(params.get('next'));
  const [attempt, setAttempt] = useState(0);
  const [unreachable, setUnreachable] = useState(false);

  // Read through a ref rather than listed as a dependency: each renewal rotates
  // the refresh token, so the effect must run once per attempt and not again
  // because a router object compared unequal on a re-render.
  const routerRef = useRef(router);
  useEffect(() => {
    routerRef.current = router;
  });

  useEffect(() => {
    let current = true;
    void refreshSession().then((outcome) => {
      if (!current) return;
      if (outcome === 'renewed') {
        routerRef.current.replace(destination);
      } else if (outcome === 'refused') {
        routerRef.current.replace(
          `/login?${new URLSearchParams({ next: destination }).toString()}`,
        );
      } else {
        setUnreachable(true);
      }
    });
    return () => {
      current = false;
    };
  }, [attempt, destination]);

  if (!unreachable) return <Checking />;

  return (
    <div className="flex flex-col items-center gap-3" role="alert">
      <p className="text-sm text-muted-foreground">
        Could not reach Aether to restore your session.
      </p>
      <Button
        variant="outline"
        onClick={() => {
          setUnreachable(false);
          setAttempt((count) => count + 1);
        }}
      >
        Try again
      </Button>
    </div>
  );
}

function Checking() {
  return (
    <p
      className="flex items-center gap-2 text-sm text-muted-foreground"
      role="status"
      aria-live="polite"
    >
      <LoaderCircle className="size-4 animate-spin" data-motion="loop" aria-hidden />
      Restoring your session
    </p>
  );
}
