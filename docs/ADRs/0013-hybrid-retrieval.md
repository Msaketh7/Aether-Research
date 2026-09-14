# ADR 0013: Hybrid retrieval - OR-ed lexical search, reciprocal rank fusion, diversity reranking

- **Status:** Accepted
- **Date:** 2026-09-11

## Context

Phase 8 turns the chunks Phase 7 wrote into search. ADR 0003 fixed the interface
(`retrieve`, `retrieve_with_filters`, `retrieve_hybrid`, ours, LlamaIndex or
anything else behind it) and ADR 0004 fixed the store (one Postgres, `pgvector`
for the dense half, a generated `tsvector` for the lexical half). What was left
open was how the two halves combine, what reranks them, and whether 512/64 is
the right chunk size - a question ADR 0012 explicitly deferred to this phase's
benchmark.

Four things shaped the decisions below, and three of them were discovered by
running the code rather than by reading it.

**`websearch_to_tsquery` combines terms with AND.** It is the obvious function
to reach for - it takes what a person types and never raises on punctuation -
and it is wrong here. Retrieval in this system is given _questions_, not keyword
lists. Measured on the fixture corpus, "inference pricing memory export"
produced `'infer' & 'price' & 'memori' & 'export'` and matched nothing at all,
because no chunk contains every one of those stems.

**HNSW returns fewer rows than asked for.** pgvector's `hnsw.ef_search`
defaults to 40, so a search for the 50 candidates the TDD specifies would
silently return 40 - fewer still once a metadata filter, which pgvector applies
after the index scan, removes some of them.

**A ranking that is not reproducible cannot be benchmarked.** Ties in
`ts_rank_cd` are common, and the first tiebreak used `document_id` - generated
per ingestion. The same corpus ingested twice ranked its tied chunks
differently, and the benchmark's MRR moved between runs of the same
measurement.

**`nomic-embed-text` is asymmetric.** The one embedding model the registry
declares is trained with a task prefix on every input, and a different one for a
stored passage and for a question asked of it. Nothing supplied them. It fails
nothing: it just puts queries slightly away from the documents they should
match, and retrieval quietly gets worse.

## Decision

### The lexical arm ORs the query's terms

The query is normalised by `to_tsvector` with the same text-search
configuration the indexed column was generated with, its lexemes are taken with
`unnest`, each is quoted with `quote_literal`, and they are joined with `|`. No
user text is concatenated into SQL: every term is a lexeme Postgres produced
from a bound parameter.

The configuration itself is named once, in `app/db/models/source.py`, and used
both to generate the column and to parse the query. A mismatch between the two
returns no rows and raises nothing, so it is the kind of defect that has to be
made impossible rather than detected.

What this gives up is the operator syntax `websearch_to_tsquery` understands:
quoted phrases, `or`, a leading `-` for exclusion. No caller generates them
today. This arm's job in a hybrid retriever is recall; precision is the
ranking's job, then fusion's, then the reranker's.

Scoring is `ts_rank_cd` under normalisation 32, which bounds the score into
[0, 1). This is **not BM25**, and the TDD's "BM25 / full-text" should be read as
the latter. Bringing in a BM25 extension would be new infrastructure, and
nothing measured yet justifies it - fusion consumes _ranks_, not scores, so what
the lexical arm has to get right is the ordering.

### Both arms narrow through one filter builder

`_chunk_selection` builds the columns, the joins, the `user_id` ownership
predicate and every metadata filter, and listing, keyword search and vector
search all start from it. A filter honoured by one path and ignored by another
would put material into a report that the caller had excluded.

### Reciprocal Rank Fusion, weighted, k = 60

The arms' scores are not comparable - one is a cosine similarity, the other a
`ts_rank_cd` value - and per-query normalisation is worse than it looks, because
it makes the top hit of a query that found nothing score as high as the top hit
of a query that found the answer. RRF reads only the order. `k = 60` is the
value from the paper that introduced the method, recorded as a documented
default.

Per-arm weights are configuration. A weight of zero removes an arm rather than
scoring it zero, so a deployment with no embeddings reports a _skipped_ arm
rather than a dense arm that appears to have searched and found nothing.

### The reranker is MMR, and it is named for what it does

The TDD describes a cross-encoder reranker. This ships Maximal Marginal
Relevance instead, over a Jaccard overlap of the candidates' own token sets.

The reason is the shape of the problem actually in front of us: the top of a
fused list is full of near-duplicates, because chunks overlap by 64 tokens by
construction, filings restate their own risk factors, and one wire story gets
reposted ten times. A top-8 that is one fact eight times scores well on every
ranking metric and starves the report of everything else.

A cross-encoder is the right eventual answer for _relevance_, which is a
different job. It is also a model this repository cannot run or price today, and
picking one before this phase's benchmark existed would be the anticipation ADR
0004 warns against. `Reranker` is async and takes the query, so a cross-encoder
or an LLM judge drops in behind it, and the benchmark says whether it earned its
latency.

### Vectors are compared only with vectors from the same model

Dense search filters on `embedding_model`. Two models' vectors share a column
but not a space, so a mixture ranks by nothing; re-embedding is how a deployment
changes model, and until it has, the older vectors are invisible rather than
wrong. `hnsw.ef_search` is raised to twice the requested candidate count so that
"top k" means k.

### Ties break on a corpus-stable key

`(canonical_url, chunk_index, id)` rather than `(document_id, chunk_index)`. The
source's canonical URL is a property of the document rather than of the row, so
the same corpus ingested twice produces the same ranking - which is what makes
a benchmark's numbers attributable to a change in code rather than to a change
in generated identifiers.

### Task prefixes are declared per model and applied by the gateway

`ModelSpec` carries `embedding_document_prefix` and `embedding_query_prefix`,
empty by default because a prefix a model was not trained with is noise added to
every vector. `LLMGateway.embed()` takes an `EmbeddingPurpose` and applies the
matching one, so a caller embeds a query by saying it is a query rather than by
knowing which models need which string. `QueryEmbedder` is built _from_ the
`ChunkEmbedder`, which makes "the query and the chunks came from the same model"
structural rather than a convention two call sites are expected to keep.

### The chunk size stays at 512/64, now for a measured reason

`scripts/benchmark_retrieval.py` ingests a corpus at several chunk sizes and
scores each. Relevance is labelled by _anchors_: verbatim passages from the
corpus that answer a question, with a chunk relevant if it contains one. That
definition is objective, re-checkable, and survives re-chunking - which is the
whole point, since the same labels then score a 256-token corpus and a 1024-token
one.

## Consequences

- Retrieval works with no embedding model configured. The lexical arm over the
  generated column stands alone, and the dense arm reports itself skipped with
  a reason rather than returning an empty list.
- A retrieval result carries its working: each arm's outcome, and each chunk's
  per-arm rank and score. "The lexical arm found this at rank 1 and the dense
  arm never saw it" is the shape of a retrieval bug and is invisible once scores
  are fused.
- The dense arm's SQL cannot be exercised on a machine without pgvector. Those
  tests skip locally with a reason and run in CI against the
  `pgvector/pgvector:pg17` image, as the Phase 7 vector tests do.
- No dense or hybrid quality number exists yet. No embedding model has been run
  against a real corpus in this repository, so the benchmark reports those rows
  as _not measured_, with the reason, rather than as strategies that scored
  badly.
- Phrase and exclusion operators in a query are not honoured by the lexical arm.
  Reintroducing them means a second tsquery and a decision about how to combine
  the two, and the benchmark is the thing that should drive it.
