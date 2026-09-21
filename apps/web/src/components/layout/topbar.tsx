'use client';

import { LogOut, Plus, Settings as SettingsIcon, User as UserIcon } from 'lucide-react';
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
import { Brand } from './brand';
import { MobileNav } from './mobile-nav';
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
    // Sticky and translucent: the page scrolling under the chrome is what tells
    // you the chrome is fixed. `z-30` sits under the mobile drawer's `z-50`.
    <header className="glass sticky top-0 z-30 flex h-14 shrink-0 items-center gap-2 border-b border-border px-3 sm:px-6">
      <MobileNav />

      <Brand href="/" className="lg:hidden" />

      <div className="flex-1" />

      <Button asChild size="sm" variant="outline" className="group lg:hidden">
        <Link href="/">
          <Plus className="group-hover:rotate-90" aria-hidden />
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
          <DropdownMenuLabel className="max-w-56 truncate">
            {user?.email ?? 'Signed in'}
          </DropdownMenuLabel>
          <DropdownMenuSeparator className="my-1 h-px bg-border" />
          <DropdownMenuItem asChild>
            <Link href="/settings">
              <SettingsIcon aria-hidden />
              Settings
            </Link>
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
