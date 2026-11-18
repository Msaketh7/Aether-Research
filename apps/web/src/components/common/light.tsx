'use client';

import { useEffect, useRef } from 'react';

/**
 * The arc of light that runs round a hero surface's edge (`.beam-ring` in
 * globals.css). Put it first inside an element with `surface-hero`; it takes
 * the surface's radius and sits under its content. The arc is a child span
 * rather than a pseudo-element so that its rotation runs on the compositor.
 *
 * It travels for as long as the surface is on screen and stops the moment it
 * scrolls out of view - the attribute is written straight onto the ring, so
 * React never re-renders for it. Without an `IntersectionObserver` (jsdom),
 * it simply runs.
 */
export function BeamRing() {
  const ring = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const element = ring.current;
    if (!element || typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(([entry]) => {
      element.toggleAttribute('data-offscreen', !entry?.isIntersecting);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return (
    <span ref={ring} className="beam-ring" aria-hidden>
      <span className="beam-arc" />
    </span>
  );
}

/**
 * A soft light under the pointer, inside the surface it is placed in.
 *
 * It moves by `transform` on its own layer, written straight to the element
 * once per frame. The first version wrote two custom properties onto the
 * card instead, and because custom properties inherit, every pointer move
 * recalculated the style of the whole form inside it. React never hears
 * about any of this: a pointer moves sixty times a second, and re-rendering
 * a form that often drops keystrokes on a slow machine.
 *
 * The surface's box is measured on entry rather than on every move, so the
 * handler never forces a layout; a scroll invalidates it, since the box has
 * moved under a pointer that has not.
 */
export function Spotlight() {
  const light = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const element = light.current;
    const host = element?.parentElement?.parentElement;
    if (!element || !host) return;

    let box: DOMRect | null = null;
    let frame = 0;

    const forget = () => {
      box = null;
    };
    const move = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse') return;
      box ??= host.getBoundingClientRect();
      // Relative to where the light rests: centred, a fifth of a card above.
      const x = event.clientX - box.left - box.width / 2;
      const y = event.clientY - box.top + box.height * 0.2;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        element.style.transform = `translate3d(${x}px, ${y}px, 0)`;
      });
    };

    host.addEventListener('pointerenter', forget);
    host.addEventListener('pointermove', move);
    window.addEventListener('scroll', forget, { passive: true, capture: true });
    return () => {
      cancelAnimationFrame(frame);
      host.removeEventListener('pointerenter', forget);
      host.removeEventListener('pointermove', move);
      window.removeEventListener('scroll', forget, { capture: true });
    };
  }, []);

  return (
    <span className="spotlight-clip" aria-hidden>
      <span ref={light} className="spotlight" />
    </span>
  );
}
