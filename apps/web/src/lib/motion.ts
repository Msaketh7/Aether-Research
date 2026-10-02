import { useSyncExternalStore } from 'react';

const REDUCED_MOTION = '(prefers-reduced-motion: reduce)';

interface NetworkInformationLike {
  saveData?: boolean;
}

function subscribe(onChange: () => void) {
  const query = window.matchMedia(REDUCED_MOTION);
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
}

function prefersReducedMotion() {
  return window.matchMedia(REDUCED_MOTION).matches;
}

/**
 * Whether ambient motion - a looping video, a cycling ticker - may run.
 *
 * A subscription rather than a read in an effect, so turning the OS setting on
 * mid-visit stops the loop at once instead of on the next load. The server
 * snapshot is `false`: the server cannot know, and starting a 6 MB video for
 * somebody who asked for stillness is the worse of the two mistakes. The client
 * corrects it straight after hydration.
 *
 * Save-Data counts as a "no" too. It is a request not to spend bandwidth, and a
 * background loop is the most expendable thing on the page.
 */
export function useAmbientMotion(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => {
      const connection = (navigator as Navigator & { connection?: NetworkInformationLike })
        .connection;
      return !prefersReducedMotion() && connection?.saveData !== true;
    },
    () => false,
  );
}

/**
 * A CSS time as milliseconds. Tokens are authored as `380ms` but the build
 * minifies them, and what the browser hands back is `.38s` - read naively as
 * a number, that is a shake lasting a third of a millisecond.
 */
export function toMilliseconds(time: string, fallback: number): number {
  const match = /^\s*(-?[\d.]+)(ms|s)\s*$/.exec(time);
  if (!match?.[1]) return fallback;
  const value = Number.parseFloat(match[1]);
  if (!Number.isFinite(value)) return fallback;
  return match[2] === 's' ? value * 1000 : value;
}

/**
 * A refusal, felt. A short horizontal shake on the element that was refused.
 *
 * Imperative (the Web Animations API) because it has to replay on every
 * refusal, and a CSS class that is already applied does not restart. The
 * duration is read from the motion tokens rather than chosen here; skipped
 * outright under reduced motion, where the error message alone carries it.
 */
export function shake(element: HTMLElement | null) {
  if (!element || typeof element.animate !== 'function' || prefersReducedMotion()) return;

  const token = getComputedStyle(element).getPropertyValue('--duration-slow');
  const duration = toMilliseconds(token, 380);

  element.animate(
    [
      { transform: 'translate3d(0, 0, 0)' },
      { transform: 'translate3d(-7px, 0, 0)' },
      { transform: 'translate3d(6px, 0, 0)' },
      { transform: 'translate3d(-4px, 0, 0)' },
      { transform: 'translate3d(2px, 0, 0)' },
      { transform: 'translate3d(0, 0, 0)' },
    ],
    { duration, easing: 'cubic-bezier(0.36, 0.07, 0.19, 0.97)' },
  );
}
