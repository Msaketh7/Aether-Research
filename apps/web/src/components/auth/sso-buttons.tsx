'use client';

import type { SsoOption } from '@aether/shared-types';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { apiHref } from '@/lib/api/config';
import { useSsoOptions } from '@/lib/api/queries';
import { CONNECTION_ICONS } from './provider-icons';

/**
 * The single sign-on buttons on the sign-in and registration pages.
 *
 * **A real link, not a fetch.** An OAuth authorization request has to be a
 * top-level navigation: the provider needs to show its own consent screen on
 * its own origin, and it sets and reads its own cookies while doing so. XHR
 * cannot do that and would be blocked by CORS in any case. It is also a plain
 * `<a>` rather than a Next `<Link>`, because `start_url` is on the API origin
 * and client-side routing has nothing useful to do with it.
 *
 * **The destination is carried, never the origin's trust.** `next` is appended
 * so a person who was sent to sign in lands back where they were going. The
 * API validates it against its own allowlist before redirecting - a value that
 * arrives here from the address bar is attacker-controlled, and an OAuth
 * callback that forwards to an arbitrary URL is a textbook open redirect.
 * Sending only a path is the frontend's half of that; refusing anything else
 * is the API's.
 */
export function SsoButtons({ next }: { next?: string }) {
  const { data, isPending, isError } = useSsoOptions();

  if (isPending) {
    return (
      <div className="flex flex-col gap-2" aria-hidden>
        <Skeleton className="h-11 w-full" />
        <Skeleton className="h-11 w-full" />
      </div>
    );
  }

  // Deliberately silent. If the deployment cannot say which providers it has,
  // the honest fallback is the password form that is already on the page - an
  // error banner here would suggest sign-in is broken when it is not.
  //
  // `Array.isArray` rather than a truthiness check on `data`: this is a parsed
  // network response, so the shape is an assumption until it is tested. A body
  // without `options` reached `.length` of `undefined` and took the whole page
  // down with it, which is a worse failure than showing no buttons.
  if (isError || !Array.isArray(data?.options) || data.options.length === 0) return null;

  return (
    <div className="flex flex-col gap-3">
      {/*
        The rule comes first because the buttons sit *below* the password form.
        Email and password are the primary path on this deployment - putting
        them first means the field a returning user is reaching for is the one
        under the cursor, and it leaves the alternatives where an alternative
        belongs. The separator has to lead the group it separates, or it reads
        as a divider belonging to the form above it.
      */}
      {data.password_enabled ? (
        <div className="flex items-center gap-3">
          <Separator className="flex-1" />
          <span className="text-xs text-muted-foreground">or continue with</span>
          <Separator className="flex-1" />
        </div>
      ) : null}

      <div className="flex flex-col gap-2">
        {data.options.map((option) => (
          <SsoButton key={`${option.provider}:${option.connection}`} option={option} next={next} />
        ))}
      </div>
    </div>
  );
}

function SsoButton({ option, next }: { option: SsoOption; next?: string }) {
  const Icon = CONNECTION_ICONS[option.connection];
  // On the API's origin, not this one: in local development they differ, and
  // a bare `/api/v1/...` here would 404 on the web app instead of starting.
  const start = apiHref(option.start_url);
  const href = next ? `${start}?next=${encodeURIComponent(next)}` : start;

  return (
    <Button asChild variant="outline" className="group h-11 w-full">
      {/*
        `rel="nofollow"` because this is a state-changing GET: following it
        mints an OAuth transaction and sets a cookie, which is not something a
        crawler or a link prefetcher should be doing on a visitor's behalf.
      */}
      <a href={href} rel="nofollow" data-testid={`sso-${option.connection}`}>
        {Icon ? <Icon className="size-4 group-hover:scale-110 group-hover:-rotate-6" /> : null}
        Continue with {option.label}
      </a>
    </Button>
  );
}
