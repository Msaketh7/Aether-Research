import { Fraunces, JetBrains_Mono, Plus_Jakarta_Sans } from 'next/font/google';

/**
 * The typefaces, in one place.
 *
 * Three families, each doing a job the others cannot:
 *
 * `Plus Jakarta Sans` carries the interface. It is a humanist geometric with
 * open apertures and a tall x-height, which is what keeps an 11px table label
 * legible; the system stack this replaces resolved to Segoe UI on Windows and
 * Helvetica on a Mac, so the product looked like a different, blander app on
 * every machine and like nobody had chosen anything.
 *
 * `Fraunces` carries the display line. It is variable on two axes this uses
 * deliberately: `SOFT` rounds the terminals and `WONK` lets the italic-flavoured
 * letterforms out, which is the whole point - it is the one place the product
 * is allowed to look drawn by a person rather than set by a machine. Headlines
 * only, and only large.
 *
 * `JetBrains Mono` carries every number. Costs, token counts, character offsets
 * and hashes all need figures of identical width or a live counter shifts the
 * layout on each tick, and its zero is slashed, which matters when a content
 * hash is being read aloud.
 *
 * All three are self-hosted by `next/font`: the files are emitted into the
 * build output and served from this origin, so a running deployment makes no
 * request to Google and there is no flash of unstyled text to design around.
 */

export const sans = Plus_Jakarta_Sans({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-sans-family',
  // No `weight`: all three are variable fonts, so the whole axis ships in one
  // file and any weight in between is available without another download.
});

export const display = Fraunces({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-display-family',
  // `wght` is the default axis and comes for free; these three are the ones
  // this design actually sets, and naming them is what keeps the rest out.
  axes: ['SOFT', 'WONK', 'opsz'],
});

export const mono = JetBrains_Mono({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-mono-family',
});

/** Applied to `<html>` so every family is available as a CSS variable. */
export const fontVariables = `${sans.variable} ${display.variable} ${mono.variable}`;
