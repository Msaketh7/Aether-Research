# ADR 0010: S3-compatible object storage behind an `ObjectStorage` interface

- **Status:** Accepted
- **Date:** 2026-09-06

## Context

A research run produces bytes that do not belong in Postgres: the PDF of a
filing, the raw HTML of a fetched page, the normalised text of a document, a
screenshot kept as visible evidence, a generated report, and the inputs and
outputs of a benchmark. Some are tens of megabytes. TDD 7.1 already states the
rule — large documents are never stored in Postgres, only a storage key — but
until now nothing implemented it.

Three properties are needed. The store must be the **same in development and in
production**, or the code path that matters is only exercised after deploy. It
must be **replaceable**, because the retrieval and ingestion phases will write
through it constantly and a hard dependency on one vendor's SDK spreads. And
every call must be **bounded and classified**, because storage sits on the
critical path of a run: an unbounded read is a run that hangs, and a
misclassified failure is a broken deployment that looks like a flaky network.

## Decision

**S3-compatible object storage**, reached through an `ObjectStorage` protocol in
`apps/api/app/storage/`.

| Environment          | Backend                                                            |
| -------------------- | ------------------------------------------------------------------ |
| Production / staging | AWS S3 (bucket from Terraform, credentials from the ECS task role) |
| Local development    | MinIO via `docker compose`                                         |
| Tests, no Docker     | `FilesystemObjectStorage`, never selected implicitly               |

The interface is `upload`, `download`, `download_stream`, `delete`, `exists`,
`stat`, `check`, `close`. One S3 implementation serves both MinIO and AWS: the
difference is an endpoint URL and an addressing style, not code.

### Key layout

```
runs/{run_id}/{kind}/{name}          kind ∈ raw-html | pdf | document | screenshot | report
evaluations/{evaluation_id}/{name}
```

Keys are derived in one module and nowhere else. Everything a run produced shares
one prefix, so deleting a user's data, expiring old artifacts and totalling a
run's storage cost are prefix operations rather than joins. The kind is in the
path, so a lifecycle rule can expire raw HTML while keeping reports.

Immutable artifacts are **content-addressed**: the name is the SHA-256 of the
bytes, which is the same value `documents.content_hash` stores. Re-fetching a
source therefore writes the same key with the same content — ingestion is
idempotent without a check-then-write race. Artifacts that are one per row (a
report, a screenshot of a source) are named by that row's id instead.

### Bounds and failure classes

Every call carries a connect timeout, a read timeout and a bounded retry policy.
A single artifact is capped (25 MiB by default) **on read as well as write**,
because a cap enforced only at upload is not a bound once presigned uploads
exist. `download_stream` is the escape hatch for artifacts that legitimately
exceed it.

Failures are translated into three classes that behave differently:

| Class                | Meaning                                       | Response |
| -------------------- | --------------------------------------------- | -------- |
| `ObjectNotFound`     | nothing at that key — a fact, not a fault     | 404      |
| `StorageUnavailable` | endpoint unreachable, timed out, 5xx — retry  | 503      |
| `StorageError`       | missing bucket, denied credentials — a defect | 500      |

No `ClientError` escapes an implementation, and no message carries the bucket
name or endpoint; that detail goes to the log under the request id
(threat model 3.5).

## Consequences

- **The store is a hard dependency of readiness.** `/ready` HEADs the configured
  bucket — not the account, which would pass on credentials alone while every
  write failed. A run that cannot persist a fetched PDF would produce a report
  whose citations point at nothing, so serving traffic without it is worse than
  refusing it.
- **Production holds no S3 credentials.** Unset keys let botocore's chain resolve
  the ECS task role (ADR 0008). Local development sets MinIO's static ones.
- **A second implementation is maintained.** The filesystem backend exists so the
  storage path is runnable and testable without Docker, and it is what keeps the
  interface from becoming an S3 client with a different name. It is refused in
  production, where artifacts on a container's disk would not survive a deploy.
- **`auto` addressing is resolved explicitly.** botocore prefers virtual-host
  addressing for a DNS-compatible bucket, which turns `http://localhost:9000`
  into `http://aether-artifacts.localhost:9000`. A custom endpoint gets path
  style; AWS gets virtual-host style.
- **Presigned URLs are deferred to Phase 7.** TDD 3.5 needs them for direct
  browser uploads, and they will extend this interface when the file API that
  consumes them is built rather than being added speculatively now.
- **`aioboto3` is a new dependency** (with `botocore`, `aiohttp` and their
  transitive set). Accepted over hand-rolling SigV4: request signing is exactly
  the kind of code that is cheap to get subtly wrong and expensive to debug.
