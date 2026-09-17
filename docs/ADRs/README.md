# Architecture Decision Records

Every decision that is expensive to reverse gets a record here. The format is
Nygard-style: context, decision, consequences. A record is immutable once
accepted; a changed mind means a new ADR that supersedes the old one.

| ID                                         | Title                                                                         | Status   |
| ------------------------------------------ | ----------------------------------------------------------------------------- | -------- |
| [0001](0001-modular-monolith.md)           | Modular monolith with separately scaled API and worker processes              | Accepted |
| [0002](0002-langgraph-orchestration.md)    | LangGraph as the agent orchestration layer                                    | Accepted |
| [0003](0003-llamaindex-ingestion.md)       | LlamaIndex for ingestion and retrieval primitives                             | Accepted |
| [0004](0004-postgres-pgvector.md)          | PostgreSQL + pgvector as the single system of record                          | Accepted |
| [0005](0005-redis-queue.md)                | Redis as queue, cache and pub/sub bus                                         | Accepted |
| [0006](0006-sse-streaming.md)              | Server-Sent Events for research progress streaming                            | Accepted |
| [0007](0007-model-routing.md)              | Provider-neutral LLM gateway with role-based model routing                    | Accepted |
| [0008](0008-aws-ecs-deployment.md)         | AWS ECS Fargate as the production deployment target                           | Accepted |
| [0009](0009-frontend-mock-transport.md)    | Frontend-first delivery against an in-process mock API                        | Accepted |
| [0010](0010-object-storage.md)             | S3-compatible object storage behind an `ObjectStorage` interface              | Accepted |
| [0011](0011-untrusted-content-boundary.md) | Retrieved content is a type, not a convention                                 | Accepted |
| [0012](0012-document-ingestion.md)         | Document ingestion: user-owned uploads, isolated parsing, offset-exact chunks | Accepted |
| [0013](0013-hybrid-retrieval.md)           | Hybrid retrieval: OR-ed lexical search, rank fusion, diversity reranking      | Accepted |
| [0014](0014-research-graph.md)             | The research graph: typed state, bounded loops, checkpoints Alembic owns      | Accepted |
| [0015](0015-agent-outputs.md)              | Agents cite by catalogue number; prompts ship inside the API package          | Accepted |
| [0016](0016-evidence-projection.md)        | The evidence tables are a projection of the checkpoint, not a second truth    | Accepted |
| [0017](0017-worker-lease.md)               | The run's own row is the worker's lease; the queue is only a doorbell         | Accepted |
