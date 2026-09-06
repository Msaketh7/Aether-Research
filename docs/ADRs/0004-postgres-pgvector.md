# ADR 0004: PostgreSQL + pgvector as the single system of record

- **Status:** Accepted
- **Date:** 2026-09-05

## Context

The data is overwhelmingly relational: a run has tasks, tasks produce claims,
claims are supported by evidence spans belonging to documents belonging to
sources, and citations join claims to sources inside report sections. Citation
validation is a join. Exactly one column - the chunk embedding - is vectorial.

A dedicated vector database (Pinecone, Weaviate, Qdrant) would put half of one
query worth of data in another system with no transactional relationship to the
other half.

## Decision

**PostgreSQL is the only system of record**, with `pgvector` for embeddings
(HNSW index, cosine ops) and a `tsvector` GIN index for lexical search. Hybrid
retrieval fuses both inside one database.

## Consequences

- Referential integrity across sources, evidence and citations is enforced by
  the database, which is exactly the property a citation validator needs.
- One backup, one migration tool (Alembic), one connection pool.
- pgvector is comfortable at the corpus sizes this system produces. If it is
  outgrown, the `Retriever` interface (ADR 0003) is the seam where an external
  index is introduced - and that call gets made from measured recall and
  latency, not from anticipation.
