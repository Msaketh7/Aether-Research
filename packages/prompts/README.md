# @aether/prompts

Versioned prompt templates for every agent role. Not yet implemented - this
package is populated in Phase 5 (model abstraction) and Phase 10 (agents).

## Contract

- One directory per agent role (`planner/`, `researcher/`, `evidence/`,
  `verifier/`, `critic/`, `synthesizer/`, `citation_validator/`).
- Every template carries a semantic `prompt_version`. That version is written to
  the `llm_calls` row for every call, so a quality change can be attributed to a
  prompt edit.
- Templates are data, never f-strings assembled at a call site.
- Retrieved document content is always injected into a clearly delimited,
  explicitly untrusted section (see [threat model](../../docs/threat-model.md)).
