"""The research graph (Phase 9) and the agents that run inside it (Phase 10).

Boundary: this module orchestrates. It calls ``retrieval``, ``sources`` and the
model gateway through their public interfaces and never touches ORM models or
HTTP concerns directly. See ADR 0002 and ADR 0014.

Deliberately empty of imports, like ``app.retrieval``: importing the package
must not pull LangGraph, psycopg and the model SDKs into a process that only
needs one value type. Import the module you need - ``app.agents.runtime`` to
run a graph, ``app.agents.schemas`` for the values it carries.
"""
