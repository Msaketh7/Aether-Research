# ADR 0018: The progress stream is numbered and stored in Postgres, fanned out by Redis

- **Status:** Accepted
- **Date:** 2026-09-17

## Context

ADR 0006 chose Server-Sent Events and said two things that Phase 14 had to make
true: a reconnecting client replays from `Last-Event-ID`, and every streamed
event is also persisted. Until now nothing but the API itself published, so the
in-process broker from Phase 2 was correct and neither promise was tested by
reality.

Phase 13 put the research graph in a separate worker process. The process that
knows what is happening is no longer the process holding the client's
connection, and there may be several of each. That breaks the in-process
broker, and it breaks a per-process sequence counter: `seq` has to be unique
and monotonic per run across every emitter, because it is the `id:` the browser
echoes back. Two processes each counting from 1 would hand two different events
the same id, and the client would silently skip one on reconnect.

A per-run Redis `INCR` is atomic and would be enough for numbering, but the
counter has to expire or it is a leak, and a counter that expires mid-run
restarts at 1. Redis is also declared as dispatch rather than truth (ADR 0005),
and making the identity of an event depend on a cache would contradict that.

## Decision

**The insert that stores an event allocates its number.** `research_events` has
a unique `(run_id, seq)`, and the append is a single
`INSERT ... SELECT COALESCE(MAX(seq), 0) + 1 ... RETURNING seq`. Two emitters
that read the same maximum collide on the constraint; the loser re-reads and
retries. Postgres is the one place that can promise uniqueness, and it is
already the system of record.

**Redis carries the event, and never decides what it is.** A pub/sub channel
per run fans an event out to whichever API replica holds the stream, with a
capped, expiring list beside it for a fast reconnect. Losing all of it costs a
client latency, not events: `history` reads rows.

**The interface is `publish(draft) -> event`.** Numbering is not something a
caller does for itself. The two-call `next_seq` then `publish` shape from Phase
2 is exactly how a sequence gets allocated in one place and used in another,
and it is removed.

**Store, then deliver.** An event delivered but not stored is one a
reconnecting client will never be told about again; an event stored but not
delivered is one the next reconnect replays. Only the second is recoverable, so
it is the failure to have. A fan-out that throws is logged and swallowed; a
store that throws is raised.

**What has already been streamed is the emitter's cursor.** The worker rebuilds
"what this run has already announced" from the run's own events, so a resumed
run continues the stream instead of re-announcing forty sources. No second
piece of state, and it works across a process replacement because the log does.

## Consequences

- One row per event, cascading with the run. A deep run emits hundreds, not
  millions; the composite index is the replay query.
- One write per event on the worker's hot path, outside the caller's
  transaction. Cheap, and it is what makes the trace reconstructible without
  the stream.
- Replay is bounded (`DEFAULT_REPLAY_LIMIT`). A client that has missed more
  than that reconnects again for the rest.
- `PERSIST_RESEARCH_EVENTS=false` is available and is a real trade: replay then
  depends on the Redis buffer, which is bounded and expires.
- Events lag their work by one node, because LangGraph reports a superstep
  after it finishes. Accepted deliberately: the alternative is announcing work
  that may not happen.
