"""Claims, evidence spans, contradictions, and the sources behind them (Phase 11).

Boundary: owns the projection of a finished run onto the relational entities the
product is served from, the deduplication that decides what counts as one
source, and the rule that conflicting evidence is recorded - never silently
resolved, and never silently un-resolved either (FR-7).

Claim *extraction* is not here: turning a passage into a keyed assertion is a
model call, and model calls live with the agents (``app.agents.extraction``).
What is here runs afterwards, over what they produced, and makes no model call
at all - which is why a claim's corroboration and a contradiction's two values
are arithmetic a reader can check rather than a judgement they have to trust.
"""
