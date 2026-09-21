'use client';

import { LLM_PROVIDERS } from '@aether/shared-types';
import { LoaderCircle, Monitor, ShieldCheck } from 'lucide-react';
import { ErrorState } from '@/components/common/error-state';
import { PageHeader } from '@/components/common/page-header';
import { SectionCard } from '@/components/common/section-card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Skeleton } from '@/components/ui/skeleton';
import { Switch } from '@/components/ui/switch';
import { API_MODE, API_BASE_URL } from '@/lib/api/config';
import {
  useCurrentUser,
  useIdentities,
  useRevokeSession,
  useSessions,
  useSettings,
  useUnlinkIdentity,
  useUpdateSettings,
} from '@/lib/api/queries';
import { formatDateTime, formatRelativeTime } from '@/lib/format';

/**
 * Profile, sessions and preferences (FR-1).
 *
 * Sessions are listed with a revoke control because "sign out this device" is
 * the one security action a user can take without an administrator.
 */
export default function SettingsPage() {
  const user = useCurrentUser();
  const sessions = useSessions();
  const revokeSession = useRevokeSession();
  const identities = useIdentities();
  const unlinkIdentity = useUnlinkIdentity();
  const settings = useSettings();
  const updateSettings = useUpdateSettings();

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5">
      <PageHeader title="Settings" description="Your account, active sessions and defaults." />

      <SectionCard title="Profile">
        {user.isPending ? (
          <Skeleton className="h-16" />
        ) : user.isError ? (
          <ErrorState error={user.error} onRetry={() => void user.refetch()} />
        ) : (
          <dl className="grid gap-3 sm:grid-cols-2">
            <div>
              <dt className="text-xs text-muted-foreground">Name</dt>
              <dd className="mt-0.5 text-sm">{user.data.name}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Email</dt>
              <dd className="mt-0.5 text-sm">{user.data.email}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Role</dt>
              <dd className="mt-0.5 text-sm capitalize">{user.data.role}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Member since</dt>
              <dd className="mt-0.5 text-sm">{formatDateTime(user.data.created_at)}</dd>
            </div>
          </dl>
        )}
      </SectionCard>

      <SectionCard title="Research defaults">
        {settings.isPending ? (
          <Skeleton className="h-24" />
        ) : settings.isError ? (
          <ErrorState error={settings.error} onRetry={() => void settings.refetch()} />
        ) : (
          <div className="flex flex-col gap-5">
            <div className="flex flex-col gap-2">
              <Label htmlFor="default-mode">Default mode</Label>
              <Select
                value={settings.data.default_mode}
                onValueChange={(value) =>
                  updateSettings.mutate({ default_mode: value as 'quick' | 'deep' })
                }
              >
                <SelectTrigger id="default-mode" className="max-w-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="quick">Quick</SelectItem>
                  <SelectItem value="deep">Deep</SelectItem>
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-2">
              <Label htmlFor="provider">Preferred provider</Label>
              <Select
                value={settings.data.preferred_provider ?? 'auto'}
                onValueChange={(value) =>
                  updateSettings.mutate({
                    preferred_provider:
                      value === 'auto' ? null : (value as (typeof LLM_PROVIDERS)[number]),
                  })
                }
              >
                <SelectTrigger id="provider" className="max-w-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">Automatic (role-based routing)</SelectItem>
                  {LLM_PROVIDERS.map((provider) => (
                    <SelectItem key={provider} value={provider} className="capitalize">
                      {provider}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                Automatic routes each agent role to its own model tier (ADR 0007). Forcing one
                provider overrides that and usually costs more for the same result.
              </p>
            </div>

            <div className="flex items-center justify-between gap-4">
              <div>
                <Label htmlFor="notify">Email me when a run finishes</Label>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Deep runs take minutes; you do not need to watch them.
                </p>
              </div>
              <Switch
                id="notify"
                checked={settings.data.notify_on_completion}
                onCheckedChange={(checked) =>
                  updateSettings.mutate({ notify_on_completion: checked })
                }
              />
            </div>

            {updateSettings.isPending ? (
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <LoaderCircle className="size-3 animate-spin" aria-hidden />
                Saving
              </p>
            ) : null}
          </div>
        )}
      </SectionCard>

      <SectionCard title="Active sessions">
        {sessions.isPending ? (
          <Skeleton className="h-24" />
        ) : sessions.isError ? (
          <ErrorState error={sessions.error} onRetry={() => void sessions.refetch()} />
        ) : (
          <ul className="flex flex-col gap-3">
            {sessions.data.map((session) => (
              <li
                key={session.id}
                className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3 last:border-0 last:pb-0"
              >
                <div className="min-w-0">
                  <p className="flex items-center gap-2 text-sm">
                    <Monitor className="size-3.5 text-muted-foreground" aria-hidden />
                    {session.user_agent}
                    {session.current ? <Badge variant="primary">This device</Badge> : null}
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {session.ip} · started {formatRelativeTime(session.created_at)} · expires{' '}
                    {formatDateTime(session.expires_at)}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  // Disabled for the current device rather than hidden: the row
                  // is what tells you which device you are on, and removing the
                  // control would make that row look like it was missing one.
                  // Signing yourself out belongs on the account menu.
                  disabled={session.current || revokeSession.isPending}
                  onClick={() => revokeSession.mutate(session.id)}
                >
                  Sign out
                </Button>
              </li>
            ))}
          </ul>
        )}
      </SectionCard>

      <SectionCard title="Linked accounts">
        {identities.isPending ? (
          <Skeleton className="h-16" />
        ) : identities.isError ? (
          <ErrorState error={identities.error} onRetry={() => void identities.refetch()} />
        ) : identities.data.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No sign-in providers are linked to this account. You sign in with your password.
          </p>
        ) : (
          <ul className="flex flex-col gap-3">
            {identities.data.map((identity) => (
              <li
                key={identity.id}
                className="flex flex-wrap items-center justify-between gap-3 border-b border-border pb-3 last:border-0 last:pb-0"
              >
                <div className="min-w-0">
                  <p className="text-sm capitalize">
                    {identity.connection ?? identity.provider}
                    <span className="ml-2 text-xs text-muted-foreground">
                      via {identity.provider}
                    </span>
                  </p>
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {identity.email ?? 'no address reported'} · linked{' '}
                    {formatRelativeTime(identity.created_at)}
                  </p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={unlinkIdentity.isPending}
                  onClick={() => unlinkIdentity.mutate(identity.id)}
                >
                  Unlink
                </Button>
              </li>
            ))}
          </ul>
        )}
        <p className="mt-3 text-xs text-muted-foreground">
          The API refuses to unlink your only way of signing in. If this is the only one and you
          have no password, set one first.
        </p>
      </SectionCard>

      <SectionCard title="Connection">
        <dl className="flex flex-col gap-2 text-sm">
          <div className="flex items-center justify-between gap-2">
            <dt className="text-muted-foreground">API mode</dt>
            <dd>
              <Badge variant={API_MODE === 'mock' ? 'warning' : 'success'}>{API_MODE}</Badge>
            </dd>
          </div>
          <div className="flex items-center justify-between gap-2">
            <dt className="text-muted-foreground">Base URL</dt>
            <dd className="font-mono text-xs">{API_BASE_URL}</dd>
          </div>
        </dl>
        <p className="mt-3 flex items-start gap-2 text-xs text-muted-foreground">
          <ShieldCheck className="mt-0.5 size-3.5 shrink-0" aria-hidden />
          Provider API keys are never sent to the browser. They are held by the API process and used
          only server-side.
        </p>
      </SectionCard>
    </div>
  );
}
