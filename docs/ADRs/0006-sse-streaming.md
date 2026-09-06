# ADR 0006: Server-Sent Events for research progress streaming

- **Status:** Accepted
- **Date:** 2026-09-05

## Context

Progress is strictly server-to-client: plan produced, source found, evidence
extracted, iteration started, report ready. The client never streams to the
server on this channel. Runs last minutes, and a user who reloads the page must
not lose the feed.

## Decision

**Server-Sent Events** over HTTP: `GET /research/{id}/events`, content type
`text/event-stream`. Each event carries a monotonic `seq`; a reconnecting client
sends `Last-Event-ID` and the API replays from a bounded Redis buffer. A comment
heartbeat every 15 s keeps proxies from closing idle streams. Every streamed
event is also persisted, so the activity trace is reconstructible without the
stream.

## Consequences

- Plain HTTP: passes through load balancers, needs no separate gateway, and the
  browser `EventSource` handles reconnection natively.
- Unidirectional only. Cancellation is a normal `POST /research/{id}/cancel`,
  which is the right shape anyway since cancel must survive a dropped
  connection.
- Per-origin HTTP/1.1 connection limits apply; acceptable because a user watches
  one run at a time. WebSockets stay available if a feature ever needs duplex.
