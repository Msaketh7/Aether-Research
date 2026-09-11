# ADR 0012: Document ingestion - user-owned uploads, isolated parsing, offset-exact chunks

- **Status:** Accepted
- **Date:** 2026-09-10

## Context

Phase 7 turns a document into part of a research run's corpus: stored, parsed,
chunked, embedded and filterable. Three facts shaped it more than the choice of
libraries did.

**Every document is hostile.** The threat model (section 3.6) names crafted PDFs
and decompression bombs, and asks for magic-byte validation, size and page caps,
a parse timeout that classifies rather than crashes, and parsing in a
resource-limited path. Parsers for PDF and HTML have long histories of bugs on
crafted input, and such a bug shows up as a hang, a memory blow-up or code
execution.

**Evidence cites offsets.** `evidence.span_start` and `span_end` point into
`documents.normalized_content`, and the citation validator re-reads the span at
those offsets. A chunk whose offsets are off by a few characters would make a
citation point at words the source never contained.

**The schema and the contract had gaps no code had yet touched.** Phase 7 is the
first writer of documents, chunks and embeddings, and the first caller of
`gateway.embed()`. Writing them exposed:

- `document_chunks.embedding` was `vector(1536)`, but the only embedding model
  the registry declares (Ollama's `nomic-embed-text`) produces 768 dimensions.
  No embedding could have been stored.
- `documents.content_hash` was unique across the whole database. The same PDF
  in two users' runs collided; had the row been shared instead,
  `evidence.document_id` cascades, so one user deleting a run would have
  deleted another user's evidence.
- `POST /research` accepted `document_ids` and silently dropped them.
- `gateway.embed()` had no gateway timeout and no retry, and a failed call left
  no record.

## Decision

### Uploads belong to users; ingestion belongs to runs

The API contract already said uploads come first: `document_ids` names files
"already uploaded via POST /files". So an upload is a user-owned row (`uploads`)
with its bytes under `uploads/{user_id}/`, attached to runs through
`research_run_uploads` when a run is created. When the run executes, each
attached upload is ingested into **that run's** corpus as an ordinary `upload`
source. A run then reads exactly like one whose sources were fetched from the
web, and no provenance chain reaches across users.

Document identity moves from global to per source: unique
`(source_id, content_hash)`. Two runs holding the same file keep two copies.
That costs storage and a re-embed, which the embedding cache (Phase 15) removes
without sharing rows. Upload identity is per user, never global, because a
global key would tell one user that another had already uploaded a given file.

### Parsing runs in a killable, scrubbed child process

Each document is parsed by `python -I -m app.retrieval.parse_worker`:

- killed at a deadline (`PARSE_TIMEOUT_SECONDS`), not abandoned;
- given an allowlisted environment with no API keys and no database URL;
- with Python-level network access refused before it reads a byte;
- on POSIX, under an address-space and CPU ceiling;
- speaking a narrow protocol: bytes in on stdin, one JSON object out, both
  bounded. Errors come back as codes mapped through a fixed table, and the
  parent re-verifies the text and page spans the child reports.

The price is about a second of process start per document.

### Readers, and where LlamaIndex fits

- **PDF:** `pypdf`, page by page. Pages are sanitised one at a time and then
  joined, so page offsets are measured on the final string. Owner-only
  encryption opens; a user password is refused.
- **HTML:** the Phase 6 extractor, so hidden markup is stripped before the
  readability pass for uploads exactly as for fetched pages.
- **Markdown and text:** decoded strictly (byte-order mark, declared charset,
  `<meta charset>`, UTF-8, then refusal - never a guess) and kept as written.
- **Language:** `py3langid`, deterministic and offline. Below 0.80 confidence
  the answer is "unknown" rather than a guess.
- **LlamaIndex** (ADR 0003) does the chunking: `SentenceSplitter` for size,
  `MarkdownNodeParser` for sections. Not its readers (they take file paths, and
  would bring more parsers into the hostile path), not its vector store (it
  would create tables beside the system of record), and not its embeddings (the
  gateway is the only door to a model, ADR 0007). It is imported lazily, because
  the import takes seconds and the API process never chunks.

### Chunks are placed, verified, and cover the document

The splitter returns chunk texts. Their positions are computed here rather than
trusted: each chunk is placed at its last occurrence inside the window a
consecutive chunk must fall in. LlamaIndex's own offsets take the first match
after the previous chunk, which in repetitive text - a run of dashes, a header
on every page - piles chunks at the start and leaves the rest uncovered. The
tests found that.

Every stored chunk satisfies `text[start:end] == chunk`, and only whitespace
falls between chunks. Both are checked on every document in production, not
just in the tests.

### Chunk size is a stated default, not a measurement

512 cl100k tokens, 64 overlap. That is about 380 words: enough context for a
passage to support a claim on its own, small enough that ten retrieved chunks
fit an evidence extractor's prompt with room to spare, and far inside the
embedding model's context. Every chunk records `sentence-v1/512/64`, so an index
built under two configurations is detectable. Phase 8's retrieval benchmark is
where the size is measured against recall.

### Two writes, and idempotent throughout

Source, document and chunks commit first, without vectors. Embeddings follow a
batch per transaction, and `embedding_model IS NULL` marks what is still
pending. A provider failure part-way costs one batch, and the retry embeds
exactly what is missing. Vectors are written with an explicit text-to-vector
cast, so no pgvector codec needs registering on the driver.

The raw copy is content-addressed. Writers of one source are serialised by a
transaction-scoped advisory lock, because at-least-once delivery (ADR 0005)
means one ingestion can run twice at the same moment.

### The vector column matches the declared model, and migrations form two lines

The column is `vector(768)`. The pipeline compares the configured model's
declared width with the column when it is built, so a mismatch fails at
startup. Changing embedding families means a migration and a re-embed.

Migrations now branch from `0001_core_schema`: `core` for relational changes,
`vector` for changes that need pgvector (0002, 0004). 0002 was split out so a
missing extension fails in one place. Chaining relational revisions after it
would have made every later table depend on pgvector, and the relational schema
could no longer be built or tested where the extension is absent. The
commands:

- `alembic upgrade heads` applies both lines;
- `alembic upgrade core@head` applies only the relational one.

### Uploads go through the API, not a presigned URL

TDD 3.5 specified presigned S3 uploads. This build streams the raw body through
the API with a hard cap, for three reasons:

- the 25 MiB ceiling is small enough to proxy;
- the magic-byte check happens before anything is stored, where a presigned
  flow can check only after the bytes are in the bucket;
- the filesystem backend, which makes the storage path runnable without
  Docker, cannot presign.

The body is the raw file, with `Content-Disposition` for the name, which keeps
a multipart parser out of the path. Presigning is the documented next step if
files outgrow the proxy.

## Consequences

- **The pipeline is complete and tested, and has no runtime caller yet.** An
  upload is stored and attached; the worker that ingests attached uploads when
  a run starts is Phase 13. Until then `AttachedUploadIngestion` is exercised
  end to end by the tests. This is the same state Phases 4 to 6 shipped in.
- **The sources endpoint still returns nothing.** The `Source` DTO requires
  `relevance_score` and `credibility_score`, which nothing measures yet. Showing
  upload sources now would display the schema's placeholder 0.50 as a
  measurement. The phase that creates sources at runtime must make those fields
  nullable or computed before wiring the endpoint.
- **New dependencies:** `llama-index-core` (bringing `nltk` and `tiktoken`
  with their data bundled, and `httpx` transitively - runtime HTTP in this
  codebase is still `httpx2` only), `pypdf` and `py3langid`. A probe confirmed
  the LlamaIndex import makes no network call and raises no warning.
- **Local development** keeps working without pgvector: the relational line
  migrates, and the vector-writing tests skip with a reason. They run in CI
  against the pgvector image.

### Residual risks, accepted and recorded

- **Windows has no resource ceiling for the parser.** There the deadline is the
  only bound. Production runs on Linux.
- **The network refusal is Python-level.** Native code could make the syscall
  directly. The scrubbed environment means it would find no credentials, and
  the deployment's egress rules are the outer layer.
- **Per-run copies duplicate storage and embedding work** for a file used in
  many runs, until the Phase 15 cache.
