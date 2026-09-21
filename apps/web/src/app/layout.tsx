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

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#fbfbfd' },
    { media: '(prefers-color-scheme: dark)', color: '#111318' },
  ],
};

/**
 * Applies the stored theme before first paint. Inline because a flash of the
 * wrong theme on every navigation is worse than one small blocking script.
 */
const THEME_SCRIPT = `
try {
  var stored = localStorage.getItem('aether-theme');
  var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  if (stored === 'dark' || (stored !== 'light' && prefersDark)) {
    document.documentElement.classList.add('dark');
  }
} catch (e) {}
`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    // `data-scroll-behavior` tells the router that the smooth scrolling set in
    // globals.css is deliberate: without it Next warns, and a route change
    // animates the scroll to the top instead of jumping there.
    <html
      lang="en"
      data-scroll-behavior="smooth"
      className={fontVariables}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="min-h-dvh bg-background text-foreground antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
