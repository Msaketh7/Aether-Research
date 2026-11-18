/**
 * The light behind every signed-in page: the sign-in stage's frame, painted.
 *
 * Three static layers, back to front - the bands of light, a scrim that holds
 * them back far enough for an hour of reading, and film grain. Fixed and
 * isolated, so it is rasterised once and then only composited: scrolling a
 * long report moves the page over it and never repaints it, and the grain's
 * blend mode never has to take part in compositing the content above.
 */
export function AppBackdrop() {
  return (
    <div className="pointer-events-none fixed inset-0 -z-10 isolate overflow-hidden" aria-hidden>
      <div className="backdrop-light absolute inset-0" />
      <div className="backdrop-scrim absolute inset-0" />
      <div className="grain absolute inset-0" />
    </div>
  );
}
