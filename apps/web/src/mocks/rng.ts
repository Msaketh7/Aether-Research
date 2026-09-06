/**
 * Deterministic pseudo-randomness for the mock API (ADR 0009).
 *
 * Fixtures must be byte-identical on every process start: Playwright asserts on
 * them, screenshots must be stable, and a flaky fixture is worse than no
 * fixture. Nothing here is used for anything security-related.
 */

/** mulberry32 - small, fast, good enough for fixture generation. */
export function createRng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Stable 32-bit hash of a string, used to seed per-entity generators. */
export function hashString(value: string): number {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

const HEX = '0123456789abcdef';

/**
 * A syntactically valid UUID v4 derived from a namespace string, so the same
 * entity always gets the same id across restarts and across test runs.
 */
export function stableUuid(namespace: string): string {
  const rng = createRng(hashString(namespace));
  const chars: string[] = [];
  for (let i = 0; i < 32; i += 1) {
    if (i === 12) {
      chars.push('4');
      continue;
    }
    if (i === 16) {
      chars.push(HEX[8 + Math.floor(rng() * 4)] as string);
      continue;
    }
    chars.push(HEX[Math.floor(rng() * 16)] as string);
  }
  const hex = chars.join('');
  return [
    hex.slice(0, 8),
    hex.slice(8, 12),
    hex.slice(12, 16),
    hex.slice(16, 20),
    hex.slice(20, 32),
  ].join('-');
}

/** A stable sha256-looking content hash. Not a real digest; display only. */
export function stableHash(namespace: string): string {
  const rng = createRng(hashString(`hash:${namespace}`));
  let out = '';
  for (let i = 0; i < 64; i += 1) out += HEX[Math.floor(rng() * 16)];
  return out;
}

/** Deterministic float in [min, max], rounded to `digits`. */
export function between(rng: () => number, min: number, max: number, digits = 2): number {
  const value = min + rng() * (max - min);
  return Number(value.toFixed(digits));
}

export function intBetween(rng: () => number, min: number, max: number): number {
  return Math.floor(min + rng() * (max - min + 1));
}

export function pick<T>(rng: () => number, items: readonly T[]): T {
  if (items.length === 0) throw new Error('pick() called with an empty list');
  return items[Math.floor(rng() * items.length)] as T;
}
