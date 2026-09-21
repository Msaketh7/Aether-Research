/**
 * Where a person goes after signing in.
 *
 * A `?next=` value arrives from the address bar, which means whoever sent the
 * link chose it. Handing that to `router.push` unchecked is an open redirect:
 * the victim signs in on a page that really is ours, and lands on a page that
 * is not - which is exactly the shape of a credential-phishing flow, and it
 * borrows our domain's credibility to do it.
 *
 * So the value is not sanitised, it is *validated*: anything that is not
 * plainly a path on this origin becomes the default. Rewriting a bad value
 * into a good one invites a bypass; rejecting it cannot be bypassed.
 *
 * The cases that matter, and why each is not caught by "starts with a slash":
 *
 * - `//evil.example` is protocol-relative. The browser reads it as an absolute
 *   URL on another host, and it starts with a slash.
 * - `/\evil.example` is treated as `//evil.example` by browsers that fold a
 *   backslash into a forward slash, which is most of them.
 * - `https://evil.example` is caught by the leading-slash rule, but is listed
 *   because it is what people test with and then assume the rest are covered.
 *
 * The API applies the same rule to the same parameter. Both halves check, on
 * purpose: the frontend's protects `router.push`, and the API's protects the
 * OAuth callback's `Location` header, which the frontend never sees.
 */

export const DEFAULT_DESTINATION = '/';

export function safeDestination(
  next: string | null | undefined,
  fallback: string = DEFAULT_DESTINATION,
): string {
  if (!next) return fallback;

  // One leading slash, and the second character may not turn it into an
  // authority. Checked on the raw string before any parsing, because parsing
  // is where the browser's own leniency would be reintroduced.
  if (!next.startsWith('/')) return fallback;
  if (next.startsWith('//') || next.startsWith('/\\')) return fallback;
  if (next.includes('\\')) return fallback;

  // A control character or a newline in a redirect target is never legitimate
  // and is how header injection is attempted against the API's half of this.
  if (/[\u0000-\u001f\u007f]/.test(next)) return fallback;

  return next;
}
