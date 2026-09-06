"""Research run lifecycle: schemas, repository boundary, event bus, service.

Boundary: owns run state transitions. It hands work to a worker through the
queue and never executes a research workflow itself (ADR 0001).
"""
