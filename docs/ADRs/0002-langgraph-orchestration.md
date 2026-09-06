# ADR 0002: LangGraph as the agent orchestration layer

- **Status:** Accepted
- **Date:** 2026-09-05

## Context

The research workflow is a directed graph with a bounded cycle: plan, fan out to
parallel researchers, extract evidence, verify, critique, conditionally re-plan,
synthesize, validate citations. It must be **resumable** - a worker restart
mid-run cannot lose three minutes of paid LLM work - and every node transition
must be observable.

Options considered: a hand-rolled asyncio state machine; a general workflow
engine (Temporal, Prefect); LangGraph.

## Decision

Use **LangGraph** as the orchestration layer, with a typed `ResearchState` and a
Postgres checkpointer.

## Consequences

- Checkpointing, interrupt/resume, parallel fan-out and conditional edges are
  provided rather than written and debugged from scratch.
- Node functions stay pure-ish: state in, state patch out. That makes each node
  unit-testable without the graph.
- We accept a framework dependency in the hot path. Mitigation: agent logic
  (prompts, tools, parsing) lives in `agents/` and `packages/prompts`, so graph
  wiring stays thin and replaceable.
- Temporal would give stronger durability guarantees at the cost of another
  service and a second programming model; rejected as premature for a system
  whose unit of work already fits in one worker process.
