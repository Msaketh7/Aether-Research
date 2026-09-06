"""Retrieval: the `Retriever` interface and its implementations (Phases 7-8).

Boundary: owns hybrid search, filtering, fusion and reranking. The interface is
ours; LlamaIndex sits behind it, so a strategy can be swapped and benchmarked
without touching agent code. See ADR 0003.
"""
