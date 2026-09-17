import { formatDistanceToNowStrict, parseISO } from 'date-fns';

/**
 * Display formatting.
 *
 * Every function here treats `null` as "not measured" and renders an em dash
 * rather than a zero. That distinction matters throughout the product: a run
 * with zero sources and a run whose sources have not been counted are different
 * facts, and the UI must never conflate them.
 */

/** Shown wherever a number was never measured, as distinct from zero. */
export const NOT_MEASURED = '—';

export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return NOT_MEASURED;
  try {
    return `${formatDistanceToNowStrict(parseISO(iso))} ago`;
  } catch {
    return NOT_MEASURED;
  }
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return NOT_MEASURED;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return NOT_MEASURED;
  return new Intl.DateTimeFormat('en-GB', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return NOT_MEASURED;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return NOT_MEASURED;
  return new Intl.DateTimeFormat('en-GB', {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
  }).format(date);
}

/** Costs are shown to cent precision below $10 and to four decimals below $1. */
export function formatCost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return NOT_MEASURED;
  if (usd === 0) return '$0.00';
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

export function formatPercent(ratio: number | null | undefined, digits = 0): string {
  if (ratio === null || ratio === undefined) return NOT_MEASURED;
  return `${(ratio * 100).toFixed(digits)}%`;
}

export function formatCount(n: number | null | undefined): string {
  if (n === null || n === undefined) return NOT_MEASURED;
  return new Intl.NumberFormat('en-US').format(n);
}

export function formatTokens(n: number | null | undefined): string {
  if (n === null || n === undefined) return NOT_MEASURED;
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return NOT_MEASURED;
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return rest === 0 ? `${minutes}m` : `${minutes}m ${rest}s`;
}

export function formatLatency(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return NOT_MEASURED;
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

/**
 * Hostname only, so a long URL does not blow out a table column.
 *
 * Anything that is not an http(s) URL is returned unchanged: `upload://a.pdf`
 * parses as a URL whose "hostname" is the filename, and silently rewriting a
 * pseudo-URL into something that looks like a domain would be misleading.
 */
export function formatDomain(url: string): string {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return url;
    return parsed.hostname.replace(/^www\./, '');
  } catch {
    return url;
  }
}

/** Shorten a uuid for display while keeping it recognisable in logs. */
export function shortId(id: string): string {
  return id.slice(0, 8);
}

export function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1).trimEnd()}…`;
}
