'use client';

import { Moon, Sun } from 'lucide-react';
import { Button } from '@/components/ui/button';

const STORAGE_KEY = 'aether-theme';

/**
 * Toggles the `.dark` class that the inline script in layout.tsx also sets.
 *
 * Deliberately stateless: the current theme lives on `<html>`, and which icon
 * shows is decided by CSS. Mirroring it into React state would mean reading the
 * DOM in an effect, which produces a hydration flash and a cascading render for
 * no benefit.
 */
export function ThemeToggle() {
  const toggle = () => {
    const next = !document.documentElement.classList.contains('dark');
    document.documentElement.classList.toggle('dark', next);
    try {
      localStorage.setItem(STORAGE_KEY, next ? 'dark' : 'light');
    } catch {
      // Private browsing; the preference simply will not persist.
    }
  };

  return (
    <Button variant="ghost" size="icon" onClick={toggle} aria-label="Toggle colour theme">
      <Sun className="dark:hidden" aria-hidden />
      <Moon className="hidden dark:block" aria-hidden />
    </Button>
  );
}
