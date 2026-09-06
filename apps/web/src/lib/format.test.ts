import { describe, expect, it } from 'vitest';
import {
  NOT_MEASURED,
  formatCost,
  formatCount,
  formatDomain,
  formatDuration,
  formatLatency,
  formatPercent,
  formatTokens,
  truncate,
} from './format';

/**
 * The behaviour these tests protect is the "not measured" contract: a metric
 * that was never measured must never render as zero, because the evaluation and
 * budget surfaces depend on the reader being able to tell the two apart.
 */
describe('formatters distinguish unmeasured from zero', () => {
  it.each([
    ['formatCost', formatCost],
    ['formatPercent', formatPercent],
    ['formatCount', formatCount],
    ['formatTokens', formatTokens],
    ['formatDuration', formatDuration],
    ['formatLatency', formatLatency],
  ])('%s renders null as an em dash', (_name, fn) => {
    expect(fn(null)).toBe(NOT_MEASURED);
    expect(fn(undefined)).toBe(NOT_MEASURED);
  });

  it('renders a genuine zero as a value, not a dash', () => {
    expect(formatCost(0)).toBe('$0.00');
    expect(formatPercent(0)).toBe('0%');
    expect(formatCount(0)).toBe('0');
  });
});

describe('formatCost', () => {
  it('uses four decimals for sub-cent amounts so a real cost is not shown as zero', () => {
    expect(formatCost(0.0042)).toBe('$0.0042');
  });

  it('uses two decimals above a cent', () => {
    expect(formatCost(1.4213)).toBe('$1.42');
  });
});

describe('formatDuration', () => {
  it.each([
    [45, '45s'],
    [60, '1m'],
    [96, '1m 36s'],
    [300, '5m'],
  ])('formats %ds as %s', (seconds, expected) => {
    expect(formatDuration(seconds)).toBe(expected);
  });
});

describe('formatTokens', () => {
  it.each([
    [842, '842'],
    [1_500, '1.5k'],
    [1_842_000, '1.84M'],
  ])('formats %d as %s', (tokens, expected) => {
    expect(formatTokens(tokens)).toBe(expected);
  });
});

describe('formatDomain', () => {
  it('strips scheme, www and path', () => {
    expect(formatDomain('https://www.sec.gov/edgar/browse?cik=1')).toBe('sec.gov');
  });

  it('returns the input unchanged when it is not a URL', () => {
    expect(formatDomain('upload://report.pdf')).toBe('upload://report.pdf');
  });
});

describe('truncate', () => {
  it('leaves short strings alone', () => {
    expect(truncate('short', 10)).toBe('short');
  });

  it('adds an ellipsis and respects the limit', () => {
    const result = truncate('a considerably longer sentence than allowed', 20);
    expect(result.length).toBeLessThanOrEqual(20);
    expect(result.endsWith('…')).toBe(true);
  });
});
