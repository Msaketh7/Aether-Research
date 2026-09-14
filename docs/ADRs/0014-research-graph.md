# ADR 0014: The research graph - typed state, bounded loops, checkpoints Alembic owns

- **Status:** Accepted
- **Date:** 2026-09-13

## Context

ADR 0002 chose LangGraph with a typed state and a Postgres checkpointer. Phase 9
builds that graph: the state, the topology, the loop control, and resumable
checkpoints - with no agents yet (Phase 10) and no worker to run it (Phase 13).

Several of the library's defaults turned out to be wrong for this system once
they were run rather than read:

**The checkpoint serializer revives any class a checkpoint names.** By default
`JsonPlusSerializer` imports and constructs whatever type is stored, logging a
warning. A checkpoint is a row in a table; anyone who can write that row chooses
what a worker constructs when it loads the run.

**An allowlisted serializer fails silently.** With an allowlist, a stored model
whose class is not on it is not refused. It comes back as a plain `dict`, and the
only sign is a log line - so a node reading `plan.subtasks` after a resume fails
far from the cause. A stored model that no longer validates is rebuilt with
`model_construct`, skipping validation entirely.

**The checkpointer migrates its own schema.** `setup()` applies ten statements and
records a version in its own `checkpoint_migrations` table, including three
`CREATE INDEX CONCURRENTLY`, which cannot run in a transaction. Left alone, the
database would have two migration histories, one applied by whichever worker
started first.

**The checkpointer is written against psycopg 3**, not the asyncpg the rest of
the service uses, and psycopg's async mode refuses Windows' default event loop.

**LangGraph's default recursion limit is 25 supersteps.** A four-round deep run
needs about 30, and the failure is an exception rather than a report.

**LangSmith reads `LANGSMITH_TRACING` from the environment itself.** Setting it
would send prompts and retrieved documents to a third party, past the typed
settings layer that is meant to be the only reader of configuration.

**The Postgres checkpointer stores primitives inline, subclasses included.** A
top-level state value that is an instance of `str`, `int`, `float` or `bool`
goes into the checkpoint's JSON column rather than through the serializer. A
`StrEnum` is a `str`, so it was written as its text and read back as a plain
string - found only on Postgres, because the in-memory saver serializes
everything. The field that exposed it held the run's stop reason, which the
graph compares with `StopReason.CANCELLED` by identity.

And one defect in this repository: `max_search_queries` was the one FR-8 ceiling
not frozen on a run. A deployment could change it under a queued run.

## Decision

### State: one TypedDict, frozen values, reducers as the concurrency contract

`ResearchState` (`app/agents/state.py`) holds the fields the build plan names -
`research_id`, `query`, `research_plan`, `subtasks`, `sources`, `claims`,
`evidence`, `contradictions`, `completed_tasks`, `failed_tasks`, `iteration`,
`token_usage`, `estimated_cost`, `critique`, `report` - plus what loop control
needs. Every value is a frozen, closed Pydantic model with bounded collections
(`app/agents/schemas.py`).

Parallel researchers write only to fields with reducers: sources merge by id,
claims and evidence replace by id (a re-scored claim replaces its candidate),
counters add, the clock keeps its furthest reading, and the first stop reason
sticks unless a cancellation overrides it.

No top-level field is a subclass of a JSON primitive. The run's parameters,
`mode` included, are one `RunParameters` model, and the stop reason and its
caveat are one `Stop` - which also means a reason and its caveat cannot
disagree. A test walks the state's annotations and fails on any such field.

### Governance lives in the node wrapper, not the agent

Each node is wrapped (`app/agents/graph.py`). At entry: the cancel flag, and for
discovery nodes the FR-8 ceilings. Around the call: a timeout - the per-node
ceiling, and for a researcher also the time left in the run. At exit: the node's
reported usage is added and the run clock advances. An agent written in Phase 10
cannot opt out.

A failure is handled by what the run can still produce: a researcher's is
recorded and the round continues; evidence, claims, verification and
contradiction failures are recorded and the run continues; a critic or later
planner failure ends discovery and synthesizes; a failed first plan, synthesis
or validation raises; a broken node contract raises. Error messages in the
trace are written by the graph, never copied from an exception that might quote
a hostile page.

LangGraph's node retry is not used: the gateway and toolbelt already retry the
calls inside a node, and retrying the node would pay for every successful call
twice.

### Loop control

`app/agents/budget.py`, pure functions. A reached ceiling - cost, runtime,
sources, searches, or rounds while the critic wants more - ends discovery, not
the run: synthesis proceeds and the report carries a caveat built from measured
values, which the graph writes into the draft itself. Cancellation ends the run.

An uncosted model call ends discovery at the end of its round. Unknown spend
cannot be held to a ceiling, so no further round starts; the round in progress
finishes, because stopping it would discard research whose cost is already
unknowable, and an unpriced planner would otherwise stop every run before it
researched anything.

Each round's researchers are dispatched by priority, capped by
`max_subtasks_per_iteration`, and handed an even share of the remaining query,
source and time budget. Runtime counts active time: the clock is re-anchored
when a process picks a run up, so a crashed worker's downtime is not charged.
`recursion_limit` is derived from the mode and `max_iterations`, with a margin,
so the budget ends a run and the limit only catches a routing defect. Citation
validation may send a draft back once.

### Topology

Deep and conversational: planner → parallel researchers (`Send`) → evidence →
claim normalization → verification → contradiction check → critic →
(planner | synthesizer) → citation validation → (synthesizer once | end).

The contradiction check is its own node, with its own boundary checks, but is
attributed to the `verifier` role: `AgentName` is shared with the database and
frontend, and no consumer needs the distinction.

Quick mode keeps evidence extraction and claim normalization, which the TDD's
trimmed graph omitted: a citation resolves through a claim to its evidence, so a
quick report without them could cite nothing. It skips verification, the
contradiction check and the critic (PRD 5.1).

### Checkpoints: Alembic owns the tables; the serializer allows only our types

Migration 0005 applies a frozen copy of the library's statements and records
the same versions, so `setup()` finds the schema current and is never called. A
test compares the copy with the installed library, so an upgrade that changes the
schema fails the build instead of drifting. The checkpointer checks the recorded
version when it opens and refuses an older schema; a newer one is tolerated with
a warning, because a rolling deploy migrates before the last old worker stops.
The copied DDL is MIT-licensed and listed in `THIRD_PARTY_NOTICES.md`.
`app/db/external.py` names the tables so autogenerate and the drift test leave
them alone.

The serializer's allowlist is derived from `ResearchState` and
`SubtaskAssignment` (a pending `Send` holds one), walking every annotation to
collect each model and enum class. Pickle fallback is off. A test round-trips a
state holding every type through the serializer and asserts each comes back as
its own type, and another shows a type off the allowlist returning as a dict.

Checkpoints are written after every node (`durability="sync"`), through a bounded
psycopg pool with the service's statement and acquisition timeouts
(`psycopg[binary]`, so no system libpq is required). The tests that open it live in
`tests/checkpointer/`, whose conftest gives them a selector event loop on
Windows. It is scoped by directory, not by marker: pytest-asyncio requires a
loop-factory hook to answer for every test it can see.

### Cancellation reads the run's status column

`POST /research/{id}/cancel` already writes `status = cancelled`. The graph reads
that column at every node boundary (`app/research/cancellation.py`). TDD 11
sketched a Redis flag; a second store holding a copy of one bit is a second thing
that can disagree. A run that is gone, or not the user's, reads as cancelled.

### Tracing is off

The runner wraps every invocation in LangSmith's `tracing_context(enabled=False)`.
Sending traces anywhere is Phase 17's decision to make deliberately.

### The search ceiling is frozen with the others

`RunLimits` gains `max_search_queries`, in Python, the shared TypeScript
contract and the mock fixtures. Migration 0006 backfills existing runs with the
documented default of 30; no run had executed, so no finished run's history is
rewritten.

## Consequences

- The graph is complete and tested, but nothing runs it: no node has an
  implementation until Phase 10, and no worker until Phase 13. The tests drive it
  with scripted test doubles.
- Two Postgres drivers in one service: asyncpg for the application, psycopg for
  checkpoints. Each has its own bounded pool.
- `langgraph` depends on `langgraph-sdk`, which pins `websockets` below 17, so it
  is now 16.1.1. uvicorn requires 13 or later.
- Cost and search ceilings are enforced from what nodes report. A node that
  raises loses its usage from the running total until Phase 16 reconciles against
  the model-call ledger.
- A library upgrade that changes the checkpoint schema fails the build until a
  migration is written for it.
- Cancellation takes effect at the next node boundary: a node already running
  finishes, or times out, before the cancellation is seen.
