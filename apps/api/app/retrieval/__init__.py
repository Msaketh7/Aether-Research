"""Retrieval: document ingestion (Phase 7) and the `Retriever` interface (Phase 8).

Boundary: owns turning a document into chunks with embeddings and metadata, and -
from Phase 8 - hybrid search, filtering, fusion and reranking over them. The
interfaces are ours; LlamaIndex sits behind them (ADR 0003), so a strategy can be
swapped and benchmarked without touching agent code.

Deliberately empty of imports. The parser child process imports
``app.retrieval.parse_worker``, and anything imported here would be imported
there too - database drivers, model SDKs - enlarging exactly the process that is
meant to hold as little as possible. Import the module you need:
``app.retrieval.factory`` for the pipeline, ``app.retrieval.uploads`` for the
file API. See ADR 0012.
"""
