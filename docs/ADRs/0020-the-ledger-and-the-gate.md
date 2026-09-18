# ADR 0020: A node execution is the unit of the ledger, and a run's ceiling is enforced at the gateway

- **Status:** Accepted
- **Date:** 2026-09-17

## Context

Three phases converge on one question: where does a number about a run come
from, and who is allowed to act on it.

Phase 16 had to make "record every LLM call and tool call" true in rows rather
than in log lines, and had to make a per-run cost ceiling actually bound
spending. The graph has checked its budget between nodes since Phase 9, which
decides correctly and bounds nothing: a node that starts under the ceiling may
finish far over it, and a researcher fanned out four ways can overshoot by four
model calls before anything looks.

Phase 17 had to make the same facts visible in aggregate, and had to attach
traces to work that happens inside a process-wide toolbelt and a process-wide
gateway - neither of which knows which run it is serving.

Phase 18 had to score a run without inventing anything, against a system whose
output is non-deterministic by construction.

## Decision

**A node execution is the unit.** One `agent_runs` row per visit to a node,
carrying its round and - for a researcher - the subtask it was given. Every
tool call and model call that node makes hangs from that row. "Which step spent
the money" becomes a query.

**The current span travels in a `contextvars.ContextVar`.** A tool call is made
deep inside a researcher, through a belt shared by the whole process; a model
call goes through a gateway with the same shape. Threading an id through every
tool signature would be plumbing for a value that is genuinely ambient - it is
_where we are_. Each researcher runs in its own task, so each gets its own copy.

**The cost ceiling is enforced in the gateway, before the call.** It is the only
other place every call passes through. A refused call raises; the graph treats
it as a _limit_ rather than as a broken agent, and the run proceeds to synthesis
with a caveat naming what stopped it.

**The step that writes the report is never refused.** FR-8 promises a partial
_result_, and the result is a report, which costs a call. A guard that refused
it would turn every budget stop into a run with nothing to show. The overshoot
is one call wide.

**An unpriced model is refused under a budget** (`REQUIRE_PRICED_MODELS`,
default on). A run whose spend cannot be measured cannot be held to a limit.
The refusal fails over to the next model in the chain.

**Telemetry never fails the work.** Every ledger write, metric observation and
span is wrapped. A research run that failed because a counter did would be an
outage caused by watching for one.

**A label is bounded or it is not a label.** Route templates are rebuilt by
substituting captured path parameters, so a run id cannot reach a Prometheus
label. Per-run facts live in the ledger, which is a database and is built for
them.

**Unmeasurable is null, in every layer.** Cost columns are nullable (migration 0011) because a model with no declared price produces _no_ cost, which is not a
cost of zero. Evaluation scorers return `None`, aggregates skip it, stored rows
omit the key, the API reports `null`, the report prints "not measured", and a
gate cannot fail on it.

**Evaluation gates ship ungated and are stored with their results.** A
threshold written before a baseline is an aspiration presented as a
requirement; a threshold not stored with its result lets a later edit turn a
past failure into a pass.

## Consequences

- Two extra writes per node and one per call, outside the caller's
  transaction. Cheap, and it is what makes a run's trace reconstructible
  without a log aggregator.
- A budget stop is not a failure: the run completes with a partial report and a
  caveat, and the failure-rate metric stays a measure of runs that went wrong.
- `/metrics` and `/evaluations` are unauthenticated like the health probes.
  Neither carries user content - only closed vocabularies, route templates, and
  questions from the committed dataset - but neither belongs on a public
  interface.
- The evaluation suite exists and has produced no numbers. Running it needs
  credentials and spends money per case; publishing a baseline that was not
  measured is the one thing this design refuses to make easy.
