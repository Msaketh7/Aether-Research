'use client';

import { Pause, Play } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useAmbientMotion } from '@/lib/motion';

/**
 * The loop behind the sign-in pages: horizontal bands of electric blue light
 * breathing on black, ten seconds, seamless.
 *
 * Hot-linked from the CDN it was delivered on, so the repository does not carry
 * a 6 MB binary. If that origin ever stops serving it, `onError` leaves the
 * painted still underneath in place and the page loses nothing but motion.
 */
export const AUTH_BACKDROP_VIDEO =
  'https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260629_021419_291eb2af-5ed4-45a0-a1d6-3ef58f4bca0b.mp4';

/**
 * Four layers, back to front: a painted still of the same light (what anyone
 * sees while the video loads, if it fails, or if they asked for no motion),
 * the video, a scrim that keeps text above it legible, and film grain so the
 * gradients do not band on an 8-bit panel.
 *
 * The video is only mounted on the client and only when ambient motion is
 * allowed, so a reduced-motion or Save-Data visitor never downloads it. It
 * fades in once its first frame has decoded rather than on mount: a black
 * rectangle replacing a painted glow for half a second reads as a glitch.
 * `loadeddata`, not `playing`, because the first frame is already the light -
 * and someone who pauses before playback starts should still see it.
 *
 * WCAG 2.2.2 requires a way to stop anything that moves for more than five
 * seconds alongside other content, hence the pause control.
 *
 * The pointer drives a slight parallax on the light (a few pixels, coalesced
 * to one write per frame). The position is written onto the parallax layer
 * itself: it used to go onto the page's root, and because custom properties
 * inherit, every pointer move recalculated the style of every element on the
 * page.
 */
export function AuthBackdrop() {
  const ambient = useAmbientMotion();
  const video = useRef<HTMLVideoElement>(null);
  const parallax = useRef<HTMLDivElement>(null);
  const [visible, setVisible] = useState(false);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    const layer = parallax.current;
    if (!ambient || !layer) return;
    let frame = 0;
    const move = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse') return;
      const { clientX, clientY } = event;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        layer.style.setProperty('--px', (clientX / window.innerWidth - 0.5).toFixed(3));
        layer.style.setProperty('--py', (clientY / window.innerHeight - 0.5).toFixed(3));
      });
    };
    window.addEventListener('pointermove', move, { passive: true });
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('pointermove', move);
    };
  }, [ambient]);

  const toggle = () => {
    const element = video.current;
    if (!element) return;
    if (element.paused) {
      void element.play()?.catch(() => setPaused(true));
      setPaused(false);
    } else {
      element.pause();
      setPaused(true);
    }
  };

  return (
    <>
      <div className="pointer-events-none fixed inset-0 isolate overflow-hidden" aria-hidden>
        <div ref={parallax} className="auth-parallax absolute inset-0">
          <div className="backdrop-light absolute inset-0" />
          {ambient ? (
            <video
              ref={video}
              className="auth-video absolute inset-0 size-full object-cover"
              data-visible={visible}
              src={AUTH_BACKDROP_VIDEO}
              autoPlay
              muted
              loop
              playsInline
              preload="auto"
              disablePictureInPicture
              disableRemotePlayback
              tabIndex={-1}
              onLoadedData={() => setVisible(true)}
              onError={() => setVisible(false)}
            />
          ) : null}
        </div>
        <div className="auth-scrim absolute inset-0" />
        <div className="grain absolute inset-0" />
      </div>

      {ambient ? (
        <button
          type="button"
          onClick={toggle}
          aria-pressed={paused}
          aria-label="Pause background animation"
          title={paused ? 'Play background animation' : 'Pause background animation'}
          className="auth-chip enter fixed right-4 bottom-4 z-20 grid size-10 place-items-center rounded-full text-foreground/80 hover:text-foreground [--enter-delay:900ms] sm:right-6 sm:bottom-6"
        >
          {paused ? (
            <Play className="size-4" aria-hidden />
          ) : (
            <Pause className="size-4" aria-hidden />
          )}
        </button>
      ) : null}
    </>
  );
}
