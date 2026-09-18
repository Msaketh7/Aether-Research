'use client';

import { FlaskConical, LogOut, Plus, User as UserIcon } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useCurrentUser, useLogout } from '@/lib/api/queries';
import { APP_NAME } from '@/lib/api/config';
import { ThemeToggle } from './theme-toggle';

export function Topbar() {
  const { data: user } = useCurrentUser();
  const router = useRouter();
  const logout = useLogout();

  // Sign-out is a request, not a navigation. Linking to /login used to leave
  // the session alive on the server and the cached research in the browser -
  // the next person at this machine would have found both (Phase 20).
  const signOut = () => {
    logout.mutate(undefined, { onSettled: () => router.push('/login') });
  };

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-4 sm:px-6">
      <Link href="/dashboard" className="flex items-center gap-2 lg:hidden">
        <FlaskConical className="size-4 text-primary" aria-hidden />
        <span className="text-sm font-semibold">{APP_NAME}</span>
      </Link>

      <div className="flex-1" />

      <Button asChild size="sm" variant="outline" className="lg:hidden">
        <Link href="/research/new">
          <Plus aria-hidden />
          New
        </Link>
      </Button>

      <ThemeToggle />

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" aria-label="Account menu">
            <UserIcon aria-hidden />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuLabel>{user?.email ?? 'Signed in'}</DropdownMenuLabel>
          <DropdownMenuSeparator className="my-1 h-px bg-border" />
          <DropdownMenuItem asChild>
            <Link href="/settings">Settings</Link>
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={signOut} data-testid="sign-out">
            <LogOut aria-hidden />
            Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}
