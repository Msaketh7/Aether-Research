# ADR 0009: Frontend-first delivery against an in-process mock API

- **Status:** Accepted
- **Date:** 2026-09-05

## Context

Phase 1 builds the product surface before any backend exists, so the UI needs
something to talk to. Two options: a client-side mock layer (functions returning
fixtures, later swapped for `fetch`), or a real HTTP surface served by the
Next.js app itself.

A client-side layer means the data path used in Phase 1 is not the data path
used in production. HTTP status handling, loading states, `EventSource`
reconnection and error boundaries would all go untested until Phase 2.

## Decision

Mock **at the network boundary**. Next.js Route Handlers under `/api/mock/v1/*`
implement the contract from TDD section 18, including a real `text/event-stream`
endpoint. Browser code always uses `fetch` and `EventSource`;
`NEXT_PUBLIC_API_MODE` selects the base URL:

- `mock` - `/api/mock/v1`, deterministic fixtures
- `live` - `NEXT_PUBLIC_API_BASE_URL`, the FastAPI service from Phase 2

Fixtures live in `apps/web/src/mocks/` and are seeded deterministically, so
tests and screenshots are reproducible.

## Consequences

- Switching to the real backend is a one-variable change, and everything above
  the base URL - query hooks, SSE reconciliation, error states, Playwright
  specs - is already exercised against real network semantics.
- Mock handlers must stay honest against the DTOs in `packages/shared-types`.
  Both sides import the same types, so contract drift is a compile error.
- Mock handlers ship in the repository. They are gated behind
  `NEXT_PUBLIC_API_MODE` and exist for local UI development and tests only, per
  the project rule that mock data disappears once a real implementation exists.
