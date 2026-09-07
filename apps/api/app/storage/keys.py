"""The artifact key namespace.

A bucket with ad-hoc keys becomes unmanageable within one release: nothing can
be listed, nothing can be lifecycled, and nothing can be deleted when a user
asks for their data to go away. So keys are *derived here and only here*, from
a fixed layout:

    runs/{run_id}/{kind}/{name}
    evaluations/{evaluation_id}/{name}

Two properties follow from that shape and both are load-bearing:

* **Everything a run produced shares one prefix.** Deleting a run, expiring old
  artifacts, or totalling a run's storage is a prefix operation rather than a
  join against the database.
* **The kind is in the path.** A lifecycle rule can expire raw HTML after 30
  days while keeping reports indefinitely, without a manifest.

Immutable artifacts are **content-addressed**: the name is the SHA-256 of the
bytes. Re-fetching the same PDF, or re-running an ingestion, writes the same key
with the same content, which makes uploads idempotent and lets the digest double
as ``documents.content_hash`` - the idempotency key the schema already uses
(TDD 7.2). Artifacts that are one-per-row - a report, a screenshot of a source -
are named by that row's id instead, because there the identity *is* the row.
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from uuid import UUID

from app.storage.errors import InvalidStorageKey

#: S3 caps a key at 1024 bytes of UTF-8. Enforced here so an over-long key fails
#: with a clear message locally instead of a signing error against a real bucket.
MAX_KEY_BYTES = 1024

#: The "safe characters" S3 documents as needing no special handling, minus the
#: ones that make keys ambiguous in a URL path. Deliberately narrow: keys are
#: generated, so there is no cost to being strict and a real cost to being lax.
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ArtifactKind(StrEnum):
    """What an artifact is, which determines where it lives and how it expires.

    The six kinds are the ones the platform actually writes; a seventh should be
    added here rather than by passing a string through.
    """

    #: The unmodified HTML of a fetched page, kept so an extraction can be
    #: re-run or audited without re-fetching (and without trusting the network
    #: to still serve the same bytes).
    RAW_HTML = "raw-html"
    #: A source document downloaded as a PDF.
    PDF = "pdf"
    #: The normalised, boilerplate-stripped form of a document.
    DOCUMENT = "document"
    #: A rendered capture of a page, for evidence a reader can see.
    SCREENSHOT = "screenshot"
    #: A generated report in its published form.
    REPORT = "report"
    #: Benchmark inputs and outputs from an evaluation run.
    EVALUATION = "evaluation"


#: The content type written when a caller does not specify one. Getting this
#: right matters beyond tidiness: it is what a browser receives from a presigned
#: download, and ``application/octet-stream`` on an HTML artifact is the
#: difference between a download and a rendered page.
DEFAULT_CONTENT_TYPES: dict[ArtifactKind, str] = {
    ArtifactKind.RAW_HTML: "text/html; charset=utf-8",
    ArtifactKind.PDF: "application/pdf",
    ArtifactKind.DOCUMENT: "application/json",
    ArtifactKind.SCREENSHOT: "image/png",
    ArtifactKind.REPORT: "text/markdown; charset=utf-8",
    ArtifactKind.EVALUATION: "application/json",
}

#: Suffix used for content-addressed names, so an operator browsing the bucket
#: can tell what a digest-named object is without opening it.
EXTENSIONS: dict[ArtifactKind, str] = {
    ArtifactKind.RAW_HTML: ".html",
    ArtifactKind.PDF: ".pdf",
    ArtifactKind.DOCUMENT: ".json",
    ArtifactKind.SCREENSHOT: ".png",
    ArtifactKind.REPORT: ".md",
    ArtifactKind.EVALUATION: ".json",
}

RUNS_PREFIX = "runs"
EVALUATIONS_PREFIX = "evaluations"


def content_digest(data: bytes) -> str:
    """SHA-256 of the bytes, hex encoded.

    The same function the ingestion pipeline uses for ``documents.content_hash``,
    so a storage key and a database row cannot disagree about identity.
    """
    return hashlib.sha256(data).hexdigest()


def content_addressed_name(kind: ArtifactKind, data: bytes) -> str:
    """``{sha256}{extension}`` - the name for an artifact that is its content."""
    return f"{content_digest(data)}{EXTENSIONS[kind]}"


def run_prefix(run_id: UUID) -> str:
    """Everything one run produced. The unit of deletion and of cost accounting."""
    return f"{RUNS_PREFIX}/{run_id}/"


def kind_prefix(run_id: UUID, kind: ArtifactKind) -> str:
    """One kind of artifact within one run."""
    return f"{RUNS_PREFIX}/{run_id}/{kind.value}/"


def run_artifact_key(*, run_id: UUID, kind: ArtifactKind, name: str) -> str:
    """The key for an artifact belonging to a research run."""
    return validate_key(f"{kind_prefix(run_id, kind)}{name}")


def evaluation_artifact_key(*, evaluation_id: UUID, name: str) -> str:
    """The key for a benchmark artifact.

    Evaluations are not scoped to a run - a benchmark spans many - so they get
    their own top-level prefix rather than being forced into the run layout.
    """
    return validate_key(f"{EVALUATIONS_PREFIX}/{evaluation_id}/{name}")


def validate_key(key: str) -> str:
    """Return ``key`` if it is safe to store under, otherwise raise.

    The filesystem backend turns a key into a path, so a key containing ``..``
    is a directory traversal that writes outside the store. That backend also
    resolves and re-checks the final path - this is the first of two independent
    barriers, not the only one, because a single check in front of a path join
    has a long history of being bypassed.

    Rejecting control characters and non-ASCII is not paranoia about S3, which
    tolerates both; it is about every tool downstream of it - a shell script, a
    log parser, a URL - that does not.
    """
    if not key:
        raise InvalidStorageKey("An artifact key cannot be empty.")

    if len(key.encode("utf-8")) > MAX_KEY_BYTES:
        raise InvalidStorageKey(
            f"An artifact key cannot exceed {MAX_KEY_BYTES} bytes.",
            context={"key_bytes": len(key.encode("utf-8"))},
        )

    if key.startswith("/") or key.endswith("/"):
        raise InvalidStorageKey("An artifact key cannot start or end with '/'.")

    if "\\" in key:
        raise InvalidStorageKey("An artifact key cannot contain a backslash.")

    for segment in key.split("/"):
        if not _SAFE_SEGMENT.match(segment):
            # Covers empty segments (`a//b`), `.`, `..`, leading dots, control
            # characters, spaces and anything non-ASCII in one rule.
            raise InvalidStorageKey(
                "An artifact key segment must be alphanumeric, '.', '_' or '-'.",
                context={"segment": segment[:64]},
            )

    return key
