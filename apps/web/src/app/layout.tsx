import type { Metadata, Viewport } from 'next';
import type { ReactNode } from 'react';
import { fontVariables } from './fonts';
import { Providers } from './providers';
import './globals.css';

export const metadata: Metadata = {
  title: {
    default: 'Aether Research',
    template: '%s · Aether Research',
  },
  description:
    'Autonomous multi-agent research: decomposition, parallel retrieval, evidence extraction, contradiction detection and citation-validated reports.',
  robots: { index: false, follow: false },
};

/** The ink the whole product stands on (`--ink`), so the browser's own
 * chrome on a phone continues the page rather than framing it in grey. */
export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: '#010207',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // `data-scroll-behavior` tells the router that the smooth scrolling set in
    // globals.css is deliberate: without it Next warns, and a route change
    // animates the scroll to the top instead of jumping there.
    //
    // Always dark: the product has one palette, the sign-in stage's. The
    // `dark` class is in the markup rather than set by a script before paint,
    // so there is no blocking script and no frame in the wrong colours.
    //
    // `suppressHydrationWarning` on both elements below, because browser
    // extensions write onto them before React hydrates and each attribute
    // surfaced as a hydration error in development: Grammarly adds
    // `data-gr-ext-installed` to <body>, and an overlay extension adds
    // `data-tsenta-overlay-build` and `data-tsenta-overlay-skip` to <html>.
    // It covers each element's own attributes only, one level deep; a real
    // mismatch anywhere inside the page is still reported. The <html> one
    // used to be there for the theme script and went with it, which is how
    // the extension's attributes started to surface.
    <html
      lang="en"
      data-scroll-behavior="smooth"
      className={`dark ${fontVariables}`}
      suppressHydrationWarning
    >
      <body
        className="min-h-dvh bg-background text-foreground antialiased"
        suppressHydrationWarning
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
