# ADR 0001: Modular monolith with separately scaled API and worker processes

- **Status:** Accepted
- **Date:** 2026-09-05
- **Deciders:** Principal engineer

## Context

Aether has two workloads with opposite shapes. HTTP requests are short, bursty
and latency-sensitive. Research runs are long (30 s to 5 min), CPU-light but
I/O-heavy, and must survive process restarts. A single process serving both
would let a research run starve the request loop; a full microservice split
would add network hops, distributed transactions and deployment surface that a
single team cannot justify at this size.

## Decision

One deployable codebase (`apps/api`) with enforced internal module boundaries
(`research/`, `agents/`, `retrieval/`, `sources/`, `evidence/`, `reports/`,
`evaluations/`, `observability/`), started as **two process types** from the
same image:

- `api` - FastAPI, serves HTTP and SSE, never executes a graph.
- `worker` - consumes the Redis queue and executes LangGraph runs.

Modules communicate through typed service interfaces, never by reaching into
another module's ORM models. Cross-process communication is the queue and the
database, never in-process function calls.

## Consequences

- One image, one migration path, one test suite; local development is a single
  `make up` plus two processes.
- API and worker scale independently: queue depth drives worker count, request
  rate drives API count.
- The module boundaries are convention, not enforced by the network. They must
  be policed in code review and by import-linting in CI.
- If a module later needs its own scaling profile (for example, embedding),
  extracting it is a deployment change rather than a rewrite, because it already
  talks over typed interfaces.
