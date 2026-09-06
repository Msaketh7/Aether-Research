# ADR 0005: Redis as queue, cache and pub/sub bus

- **Status:** Accepted
- **Date:** 2026-09-05

## Context

Three needs: hand a research job from API to worker; cache expensive
deterministic results (search responses, fetched URLs, embeddings); relay
progress events from a worker to whichever API process holds the SSE connection.

## Decision

A single **Redis** deployment serves all three, with distinct key namespaces and
eviction policies.

| Use | Mechanism | Namespace | Eviction |
|---|---|---|---|
| Job queue | Reliable list/stream with visibility timeout | `queue:research` | none |
| Cache | Keys hashed on content | `cache:{kind}:{sha256}` | TTL + LRU |
| Progress bus | Pub/Sub channel plus bounded replay buffer | `run:{id}:events` | capped list |

Job payloads carry only a `run_id`; all state lives in Postgres, so a redelivered
job is idempotent.

## Consequences

- One operational dependency instead of three.
- Redis is not a durable queue by default. Mitigation: the queue is the dispatch
  mechanism, not the source of truth. A lost message is recovered by a
  reconciliation sweep over runs stuck in `queued` or `running`.
- Cache and queue share a failure domain. Accepted: the cache is optional by
  design and every cache read has a miss path.
