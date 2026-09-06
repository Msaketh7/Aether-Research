# ADR 0007: Provider-neutral LLM gateway with role-based model routing

- **Status:** Accepted
- **Date:** 2026-09-05

## Context

A deep research run makes dozens of model calls of very different difficulty:
splitting a question into subtasks is easy; judging whether a quote actually
supports a claim is hard. Routing everything to the strongest model burns the
per-run budget on trivial calls; routing everything to the cheapest degrades the
judgements that matter. Providers also fail, rate-limit and reprice, and local
development should not require a paid key.

## Decision

One `LLMProvider` interface (`generate`, `generate_structured`, `stream`,
`embed`) implemented for OpenAI, Anthropic and Ollama, fronted by a
`ModelRegistry` (declared models, capabilities, prices) and a `ModelRouter` that
selects a model from **agent role, research mode and cost tier**, with declared
fallbacks.

Indicative routing: planner to the cheap tier; researcher to the mid tier;
verifier and critic to the strong tier; synthesizer to the strongest configured
tier. Agent nodes never construct a provider - they ask the gateway for a role.

## Consequences

- Cost versus quality becomes a configuration decision, measurable by the
  evaluation suite, instead of a hard-coded string inside an agent.
- Ollama support means the whole graph runs locally with no API key.
- Every call passes one choke point, which is where token accounting, cost
  attribution, caching, timeouts, retries and tracing are implemented once.
- The interface is the lowest common denominator across providers.
  Provider-specific features must be added deliberately as capability flags.
