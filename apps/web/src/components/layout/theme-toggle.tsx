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
 *
 * The two icons are stacked in one box and cross-rotate, so the control shows
 * a change of state rather than swapping one glyph for another - which at this
 * size is indistinguishable from the icon flickering.
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
    <Button
      variant="ghost"
      size="icon"
      onClick={toggle}
      aria-label="Toggle colour theme"
      className="relative overflow-hidden"
    >
      <Sun
        className="absolute rotate-0 scale-100 transition-[transform,opacity] duration-[var(--duration-slow)] ease-[var(--ease-spring)] dark:-rotate-90 dark:scale-0 dark:opacity-0"
        aria-hidden
      />
      <Moon
        className="absolute rotate-90 scale-0 opacity-0 transition-[transform,opacity] duration-[var(--duration-slow)] ease-[var(--ease-spring)] dark:rotate-0 dark:scale-100 dark:opacity-100"
        aria-hidden
      />
    </Button>
  );
}
