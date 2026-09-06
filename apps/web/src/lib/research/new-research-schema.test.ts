import { describe, expect, it } from 'vitest';
import {
  NEW_RESEARCH_DEFAULTS,
  fieldErrorsOf,
  newResearchSchema,
  normalizeDomain,
  toCreateRequest,
} from './new-research-schema';

describe('newResearchSchema', () => {
  const valid = {
    ...NEW_RESEARCH_DEFAULTS,
    question: 'Compare the major AI inference infrastructure providers.',
  };

  it('accepts a well-formed request', () => {
    expect(newResearchSchema.safeParse(valid).success).toBe(true);
  });

  it('rejects a question that is too short to decompose', () => {
    const result = newResearchSchema.safeParse({ ...valid, question: 'too short' });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(fieldErrorsOf(result.error).question?.[0]).toMatch(/at least 15/);
    }
  });

  it('rejects a URL where a bare domain is expected', () => {
    const result = newResearchSchema.safeParse({
      ...valid,
      domains: ['https://sec.gov/edgar'],
    });
    expect(result.success).toBe(false);
    if (!result.success) expect(fieldErrorsOf(result.error).domains).toBeDefined();
  });

  it('rejects an inverted date range', () => {
    const result = newResearchSchema.safeParse({
      ...valid,
      dateRangeStart: '2026-06-01',
      dateRangeEnd: '2026-01-01',
    });
    expect(result.success).toBe(false);
    if (!result.success) {
      expect(fieldErrorsOf(result.error).dateRangeEnd?.[0]).toMatch(/end date/i);
    }
  });

  it('accepts a date range where only one end is set', () => {
    expect(newResearchSchema.safeParse({ ...valid, dateRangeStart: '2026-01-01' }).success).toBe(
      true,
    );
  });
});

describe('toCreateRequest', () => {
  const base = {
    ...NEW_RESEARCH_DEFAULTS,
    question: '  Compare the major AI inference providers.  ',
  };

  it('trims the question and omits empty optional fields', () => {
    const request = toCreateRequest(base);
    expect(request.question).toBe('Compare the major AI inference providers.');
    expect(request).not.toHaveProperty('domains');
    expect(request).not.toHaveProperty('date_range_start');
  });

  it('omits depth for quick runs, which do only one pass', () => {
    expect(toCreateRequest({ ...base, mode: 'quick' })).not.toHaveProperty('depth');
    expect(toCreateRequest({ ...base, mode: 'deep', depth: 4 }).depth).toBe(4);
  });

  it('passes through domains and dates when present', () => {
    const request = toCreateRequest({
      ...base,
      domains: ['sec.gov'],
      dateRangeStart: '2026-01-01',
      dateRangeEnd: '2026-06-01',
    });
    expect(request.domains).toEqual(['sec.gov']);
    expect(request.date_range_start).toBe('2026-01-01');
    expect(request.date_range_end).toBe('2026-06-01');
  });
});

describe('normalizeDomain', () => {
  it.each([
    ['https://www.SEC.gov/edgar/browse', 'sec.gov'],
    ['  arxiv.org  ', 'arxiv.org'],
    ['http://github.com/vllm-project', 'github.com'],
  ])('normalizes %s to %s', (input, expected) => {
    expect(normalizeDomain(input)).toBe(expected);
  });

  it.each(['', 'not a domain', 'localhost'])('rejects %s', (input) => {
    expect(normalizeDomain(input)).toBeNull();
  });
});
