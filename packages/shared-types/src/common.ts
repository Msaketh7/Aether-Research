/**
 * Primitives shared by every DTO.
 *
 * These mirror the Postgres schema in docs/TDD.md section 7.2. When the API is
 * implemented in Phase 2 its Pydantic models must serialize to exactly these
 * shapes; the mock handlers in apps/web are typed against the same file, so a
 * contract drift shows up as a compile error rather than a runtime surprise.
 */

/** UUID v4, as returned by the API. */
export type Uuid = string;

/** ISO-8601 timestamp with timezone, e.g. `2026-09-05T12:34:56.000Z`. */
export type IsoDateTime = string;

/** ISO-8601 calendar date, e.g. `2026-09-05`. */
export type IsoDate = string;

/** A score in [0, 1]. Confidence values are calibrated, not raw model logits. */
export type UnitInterval = number;

/**
 * Cursor pagination. Every list endpoint is bounded; there is no unpaginated
 * list anywhere in the API.
 */
export interface Page<T> {
  items: T[];
  /** Opaque cursor for the next page, or `null` when the list is exhausted. */
  next_cursor: string | null;
  /** Total matching rows when cheap to compute, otherwise `null`. */
  total: number | null;
}

/** Machine-readable error envelope returned for every non-2xx response. */
export interface ApiErrorBody {
  error: {
    /** Stable, greppable code such as `run_not_found` or `budget_exceeded`. */
    code: string;
    /** Human-readable message safe to display. Never contains internals. */
    message: string;
    /** Field-level validation detail, keyed by dotted field path. */
    details?: Record<string, string[]>;
    /** Trace id, so a user-reported error can be found in the traces. */
    trace_id?: string;
  };
}
