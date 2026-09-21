import { describe, expect, it } from 'vitest';
import { DEFAULT_DESTINATION, safeDestination } from './redirect';

/**
 * The open-redirect guard.
 *
 * Every case here is a real bypass of the naive check ("does it start with a
 * slash?"), which is why the implementation does not use that check alone.
 */
describe('safeDestination', () => {
  it('keeps an ordinary path', () => {
    expect(safeDestination('/research/abc')).toBe('/research/abc');
  });

  it('keeps a path with a query string', () => {
    expect(safeDestination('/research?tab=evidence')).toBe('/research?tab=evidence');
  });

  it('falls back when there is no value', () => {
    expect(safeDestination(null)).toBe(DEFAULT_DESTINATION);
    expect(safeDestination(undefined)).toBe(DEFAULT_DESTINATION);
    expect(safeDestination('')).toBe(DEFAULT_DESTINATION);
  });

  it('refuses an absolute URL', () => {
    expect(safeDestination('https://evil.example/pwn')).toBe(DEFAULT_DESTINATION);
  });

  it('refuses a protocol-relative URL, which starts with a slash', () => {
    // The case a leading-slash check lets straight through.
    expect(safeDestination('//evil.example')).toBe(DEFAULT_DESTINATION);
  });

  it('refuses a backslash authority, which browsers fold into //', () => {
    expect(safeDestination('/\\evil.example')).toBe(DEFAULT_DESTINATION);
  });

  it('refuses a backslash anywhere in the path', () => {
    expect(safeDestination('/research\\@evil.example')).toBe(DEFAULT_DESTINATION);
  });

  it('refuses a newline, which is how header injection is attempted', () => {
    expect(safeDestination('/dashboard\nSet-Cookie: a=b')).toBe(DEFAULT_DESTINATION);
  });

  it('refuses a scheme that is not http', () => {
    expect(safeDestination('javascript:alert(1)')).toBe(DEFAULT_DESTINATION);
  });

  it("uses the caller's fallback when one is given", () => {
    expect(safeDestination(null, '/settings')).toBe('/settings');
  });
});
