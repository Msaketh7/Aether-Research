# ADR 0003: LlamaIndex for ingestion and retrieval primitives

- **Status:** Accepted
- **Date:** 2026-09-05

## Context

Document ingestion (PDF/HTML/Markdown/TXT parsing, chunking, metadata
extraction, embedding, indexing) and hybrid retrieval are solved, tedious and
easy to get subtly wrong. Hand-rolling PDF text extraction and chunk-overlap
logic buys nothing.

## Decision

Use **LlamaIndex** for readers, node parsers, embedding pipelines and
retriever/postprocessor abstractions, backed by the pgvector store that already
holds the rest of the system of record (ADR 0004).

The `Retriever` interface in `apps/api/app/retrieval` is ours, not LlamaIndex's:
`retrieve()`, `retrieve_with_filters()`, `retrieve_hybrid()`. LlamaIndex sits
behind it.

## Consequences

- Faster and better-tested ingestion than a hand-rolled pipeline.
- Two frameworks (LangGraph and LlamaIndex) in one codebase. The split is by
  responsibility and is documented: **LangGraph orchestrates, LlamaIndex ingests
  and retrieves.** Neither leaks into the other layer.
- Owning the retriever interface means a benchmark can compare a LlamaIndex
  strategy against a raw-SQL strategy without touching agent code.
