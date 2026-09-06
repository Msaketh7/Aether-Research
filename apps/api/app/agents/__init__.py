"""LangGraph nodes and agent implementations (Phases 9-10).

Boundary: this module orchestrates. It calls `retrieval`, `sources` and the
model gateway through their public interfaces and never touches ORM models or
HTTP concerns directly. See ADR 0002.
"""
