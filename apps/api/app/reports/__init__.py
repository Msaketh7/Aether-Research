"""Report schema, synthesis assembly and citation validation (Phase 12).

Boundary: owns the rule that a citation is emitted only when the chain
citation -> claim -> evidence -> document -> source resolves. That check is what
makes "never fabricate citations" a property of the system rather than a hope
about the model.
"""
