import { describe, expect, it } from 'vitest';
import { toMilliseconds } from './motion';

/**
 * The motion tokens are authored as `380ms`, but the build minifies them and
 * the browser reports `.38s`. Reading that with `parseFloat` gave a shake that
 * lasted 0.38 ms - found by checking the computed token in a real browser, not
 * by reading the stylesheet.
 */
describe('toMilliseconds', () => {
  it('reads the minified seconds form the browser actually returns', () => {
    expect(toMilliseconds('.38s', 0)).toBe(380);
    expect(toMilliseconds(' 0.56s ', 0)).toBe(560);
  });

  it('reads milliseconds as written', () => {
    expect(toMilliseconds('240ms', 0)).toBe(240);
  });

  it('falls back when the token is missing or not a time', () => {
    expect(toMilliseconds('', 380)).toBe(380);
    expect(toMilliseconds('fast', 380)).toBe(380);
    expect(toMilliseconds('12px', 380)).toBe(380);
  });
});
