# ADR 0015: Agents cite by catalogue number, and prompts ship inside the API package

- **Status:** Accepted
- **Date:** 2026-09-16

## Context

Phase 9 left every node of the research graph as a Protocol. Phase 10 implements
the nine agents behind them, and doing so forces two decisions that are
expensive to reverse later: how a model refers to the run's material, and where
an agent's instructions live.

**A model that emits identifiers cannot be checked.** The graph's state is a
web of UUIDs - sources, documents, evidence spans, claims, contradictions - and
every agent after the researcher works by relating them. The obvious design is
to show a model the ids and ask for ids back. It does not survive contact with
the product's central promise. A UUID a model invented is indistinguishable in
shape from one the system issued, and there is nowhere downstream that can tell
them apart: a claim citing a fabricated evidence id is a claim with no evidence,
and a report citing it is a report with a fabricated citation. The failure is
silent, and it is exactly the failure this system exists to prevent.

**A quote a model reports is not a span.** `EvidenceItem` carries character
offsets into the stored document's normalised text, and those offsets are what
make a citation checkable - a reader, or Phase 12's validator, re-reads the
document at them. A model asked for offsets will produce plausible integers.

**Prompt templates have to be somewhere the deployed artifact contains.** The
TDD's repository layout puts them in `packages/prompts/`, beside the shared
TypeScript types. The API's wheel packages `app` and nothing else
(`[tool.hatch.build.targets.wheel]`), so templates outside it would be present
in a developer's checkout and absent from the image - and the failure mode is a
worker that starts, takes a run, and cannot build a prompt.

**A system prompt that varies per call has no identity.** Every model call
writes `prompt_version` to its ledger row so that a change in output quality can
be attributed to a prompt edit. That is only true if the version names one
string. It is also what makes provider-side prompt caching possible (TDD 15.6).

## Decision

### A model refers to the run's material by catalogue number, never by id

Every agent builds a numbered catalogue from the state
(`app/agents/catalog.py`), renders it into the prompt, and the model answers
with numbers. The agent maps them back. The schemas a model fills in live in
`app/agents/outputs.py`, separate from the state schemas in
`app/agents/schemas.py`, and no field in them is a UUID.

A number out of range names nothing, so a fabricated reference is _visible_: it
is dropped, counted, and logged. Number 40 of a 12-item catalogue is a
fabrication anyone can see; a fabricated UUID is not.

Catalogues are pure functions of the state and are **sorted**, not taken in
insertion order. Parallel researchers write in completion order, which differs
between a run and its resume. The citation validator rebuilds the synthesizer's
claim catalogue from the same state and must get the same numbers, or every
`[n]` in the report would resolve to a different claim - while still validating,
because each number would still name something.

Catalogues are bounded, and what is dropped when one is cut is chosen by a
stated rule: refuted and contested claims are kept ahead of low-confidence
candidates, because a report that omits a refutation is wrong in a way the
reader cannot detect.

### Offsets are computed from where a quote was found, never reported

The evidence extractor names a passage and quotes it. The agent looks the quote
up in that passage's text, character for character, and computes the offsets
from the match. A quote that is not there is discarded: a paraphrase has no
offsets and an invention has no source. Matching is exact - a near match would
yield offsets pointing at _almost_ the quoted text, and a citation a reader
cannot reproduce is worse than one that was never made.

`EvidenceItem` now refuses a span whose offsets do not bracket exactly its text,
so the invariant is a property of the value rather than a rule an extractor
remembers.

### Derived identity: the same assertion is the same claim

A claim's id is `uuid5` over the run and its normalized key; a contradiction's
is `uuid5` over the run and its ordered pair. The graph's reducers merge by id,
so the same assertion found in a later round updates the claim and accumulates
its evidence rather than creating a second copy with half the support - and the
same disagreement found twice is recorded once, whichever way round it is
reported.

### The citation validator makes no model call

TDD 4.2 allows "deterministic checks plus one LLM repair pass". The repair pass
is the graph sending the draft back to the synthesizer with instructions; the
check itself is arithmetic over the state - the marker resolves to a claim, the
claim has evidence, the evidence belongs to a source the run retrieved. A
validator with a model in it is a validator that can be talked out of a
rejection by the text it is validating.

For the same reason the synthesizer may not write the `references` or `evidence`
sections. They are assembled from the run's own records in Phase 12. A model
writing a list of sources is the most reliable way to get citations to documents
that do not exist.

### Prompt templates are package data under `app/agents/prompts/`

One Markdown file per agent step, with `@version`, `@system` and `@user`
directives (`app/agents/prompting.py`). They ship in the wheel with
`registry.yaml`, which is the precedent. `packages/prompts/` remains in the
repository layout and its README points here; nothing else consumes prompts, so
a shared package would be a directory that exists to match a diagram.

The parser refuses a placeholder in a system instruction, and `render` refuses a
missing value and an unused one alike - both are the same drift between a
template and its call site, and both are otherwise silent: one sends the model a
literal `{{claims}}`, the other quietly drops the claims.

### Agents add no bounds of their own

A model call is already bounded three times: the gateway's per-request timeout,
bounded retries and declared failover chain (ADR 0007); the toolbelt's
equivalent for tool calls; and the graph's per-node timeout, cancel check and
FR-8 ceilings (ADR 0014). A fourth timeout inside each agent would be a fourth
number to keep consistent with the other three. What agents own is _how many
calls they make_, which is bounded per agent and from the run's allowances.

### The planner proposes a channel; the router resolves it

`Subtask` gains a `channel`, because one graph node runs every subtask and has
to know which researcher to be. The planner is the only step that has read the
question, so it proposes; resolution is deterministic and narrowing only - a
channel this deployment did not configure, or documents for a run with no
attached corpus, falls back to the web, and the change is logged with both.

## Consequences

- A model can no longer fabricate a reference that survives. It can still
  fabricate _prose_, which is what the citation validator and the verifier are
  for, and what Phase 18 measures.
- Catalogue ordering is now load-bearing. Changing how claims are sorted changes
  the numbers a stored report's markers resolve to, so a report persisted by
  Phase 12 must store its resolved citations rather than re-derive them.
- The numbered catalogue costs tokens: every prompt carries the material it
  refers to. That is bounded per catalogue and is the direct cost of the
  guarantee.
- Extraction is strict about quotes, so a model that paraphrases produces no
  evidence at all rather than unverifiable evidence. The drop is counted and
  logged, which makes it measurable in Phase 18 rather than invisible.
- Prompts are versioned in one file each, so a version bump is a diff rather
  than a new file. Git holds the history; `prompt_version` names what produced a
  result.
- `packages/prompts/` holds no templates. The README says where they are.
