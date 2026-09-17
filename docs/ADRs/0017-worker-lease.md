# ADR 0017: The run's own row is the worker's lease; the queue is only a doorbell

- **Status:** Accepted
- **Date:** 2026-09-17

## Context

Phase 13 gives the system its second process type (ADR 0001): a worker that
consumes the research queue and executes the graph. Everything above it already
exists - the API enqueues, the graph runs and checkpoints, the projections write
the rows a reader opens - so the decision here is narrow and entirely about
failure: **who is allowed to run a given research run, and how does the system
find out when that answer has changed?**

Four things can change it, and all four are ordinary rather than exceptional:

- a job is delivered twice, because ADR 0005 chose an at-least-once queue;
- a worker is killed mid-run - a deploy, an OOM, a spot reclaim;
- a user cancels a run that is already halfway through;
- Redis restarts and forgets a job that was never executed.

ADR 0005 already answered the last one in principle: "the queue is the dispatch
mechanism, not the source of truth. A lost message is recovered by a
reconciliation sweep over runs stuck in `queued` or `running`." This record
decides the mechanism the other three share, because they are the same question
asked from different directions.

The obvious alternative is to make Redis authoritative: a reliable queue with an
in-flight list and a visibility timeout, or a stream with a consumer group and
`XAUTOCLAIM`. Both are well understood, and both put a _second_ record of who
owns a run beside the one Postgres already has.

## Decision

**A run is owned by whichever worker holds its row.** `research_runs` gains
`worker_id`, `heartbeat_at`, `attempts` and `next_attempt_at` (migration 0009).
Taking a run is one conditional `UPDATE ... WHERE ... RETURNING`; a worker that
changed no row did not get it and drops the job. Every later write the worker
makes names its own `worker_id` and excludes terminal statuses, so a worker whose
lease expired while it was stalled cannot overwrite the state of the worker that
took over from it, and neither can overwrite a user's cancellation.

**The lease is renewed at node boundaries.** LangGraph is streamed rather than
awaited, so each superstep ends with one write that renews `heartbeat_at` and
records where the run has got to. A lease that has not been renewed within
`WORKER_LEASE_SECONDS` is claimable by anyone. That setting is required to be at
least twice `GRAPH_NODE_TIMEOUT_SECONDS`, or a healthy worker's run would be
taken from it mid-node.

**Redis only says "something happened".** `RESEARCH_QUEUE` stays a list.
`enqueue` is `RPUSH`, `reserve` is `BLPOP`, and there is no acknowledgement,
because the message carries nothing that is not already in Postgres. Every
worker periodically asks Postgres which runs _should_ be on the queue - queued
long after creation, a retry that has come due, a lease that has expired - and
pushes them back.

**An attempt is counted when a run is claimed, not when one fails.** A worker
killed mid-run therefore spends one, which is what bounds a run that crashes its
worker. A worker that is _asked_ to stop hands the run back as `paused` and
returns the attempt, because a deploy is not a failed attempt.

**`paused` means resumable.** A retryable failure, and a run handed back by a
stopping worker, both land there with the reason attached rather than in
`failed`. The status vocabulary has carried `paused` since Phase 1 for this.

## Consequences

- One place answers "who owns this run", and it is the same row the API reads.
  There is no state in Redis that can disagree with Postgres, so a Redis restart
  costs latency (until the next sweep) and nothing else.
- Correctness does not depend on the queue's delivery guarantees. A stronger
  queue would make duplicates rarer; it would not make the claim unnecessary,
  because a sweep that re-dispatches a run whose worker _might_ be alive is
  deliberate, not accidental.
- The sweep is a poll. Recovery from a dead worker takes up to
  `WORKER_LEASE_SECONDS`, and recovery from a lost message up to
  `WORKER_QUEUED_GRACE_SECONDS`. Both are configuration, and both trade latency
  for never needing a distributed lock.
- Every worker sweeps, without coordination, because re-dispatching a run that
  is already running is harmless. At a scale where the duplicate queries matter,
  a leader election or a partitioned sweep is the documented next step; nothing
  above depends on which.
- The heartbeat is one `UPDATE` per node, roughly fifteen per run. It carries the
  run's live counts as well, which is what lets the product show progress at all.
- A poison run - one that kills its worker every time - is retried
  `WORKER_MAX_ATTEMPTS` times and then failed, rather than forever.
