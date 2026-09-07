"""The ``ObjectStorage`` interface.

Postgres is the system of record; this is the warehouse next to it (TDD 7.1).
Bulky bytes - PDFs, raw HTML, normalised documents, screenshots, reports and
evaluation artifacts - live here, and the database keeps only a key.

The interface is a ``Protocol`` rather than an abstract base class for the same
reason ``JobQueue`` is: nothing needs to inherit from it, an implementation is
structurally compatible or it is not, and a test double is a plain class. Two
implementations ship - ``S3ObjectStorage`` and ``FilesystemObjectStorage`` -
which is what keeps the abstraction honest rather than an S3 client wearing a
different name.

Every method either returns a typed value or raises from
``app/storage/errors.py``. No method returns ``None`` to mean "it broke", and no
provider exception (``ClientError``, ``OSError``) escapes an implementation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

#: Read size for streamed downloads. 1 MiB is large enough that the per-chunk
#: overhead disappears and small enough that a hundred concurrent streams do not
#: add up to a memory problem.
DEFAULT_CHUNK_SIZE = 1024 * 1024

DEFAULT_CONTENT_TYPE = "application/octet-stream"


@dataclass(frozen=True, slots=True)
class ObjectStat:
    """What is known about a stored object without reading it.

    One HEAD request. ``exists()`` is this call with the result thrown away, so
    a caller that needs the size or the type should ask for the stat directly
    rather than paying for two round trips.
    """

    key: str
    size_bytes: int
    content_type: str
    #: The store's opaque version identifier. For a single-part S3 upload this
    #: is the MD5 of the content; it is treated as opaque anyway, because that
    #: equivalence stops holding for multipart uploads and for SSE-KMS buckets.
    etag: str
    last_modified: datetime | None = None
    #: The user metadata supplied at upload. Provenance a later phase attaches
    #: to an artifact - which source, which fetch - has to survive the round
    #: trip, or the artifact stops being self-describing once it is separated
    #: from the row that pointed at it.
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StoredObject:
    """The receipt for a completed upload.

    Returned rather than discarded so a caller can persist the key and size
    against a database row in the same unit of work, instead of issuing a stat
    to learn what it just wrote.
    """

    key: str
    size_bytes: int
    content_type: str
    etag: str


@runtime_checkable
class ObjectStorage(Protocol):
    """What the platform needs from a blob store, and nothing more.

    Deliberately absent: listing, copying, and presigned URLs. Presigning is the
    one real omission - TDD 3.5 needs it for direct browser uploads - and it is
    left to Phase 7, where the file API that consumes it is built, rather than
    added speculatively to an interface with no caller.
    """

    async def upload(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = DEFAULT_CONTENT_TYPE,
        metadata: Mapping[str, str] | None = None,
    ) -> StoredObject:
        """Write ``data`` at ``key``, replacing anything already there.

        Overwrite rather than error: keys are content-addressed or row-scoped,
        so a repeated write is a retry, and failing it would make ingestion
        non-idempotent for no benefit.

        Raises ``ObjectTooLarge`` above the configured ceiling,
        ``InvalidStorageKey`` for an unsafe key, ``StorageUnavailable`` if the
        store cannot be reached.
        """
        ...

    async def download(self, key: str) -> bytes:
        """Read the whole object into memory.

        Bounded by the same ceiling as ``upload``: an object that grew past it
        by another route raises ``ObjectTooLarge`` rather than being read.
        Use ``download_stream`` when the size is not known to be small.

        Raises ``ObjectNotFound`` if there is nothing at ``key``.
        """
        ...

    def download_stream(
        self,
        key: str,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> AsyncIterator[bytes]:
        """Yield the object in chunks, holding one chunk at a time.

        The unbounded-size escape hatch: a 400 MB filing does not have to fit in
        memory to be parsed or proxied to a client.
        """
        ...

    async def delete(self, key: str) -> bool:
        """Remove the object. Returns whether it was there to remove.

        Idempotent: deleting a key that does not exist is a success that returns
        ``False``, not an error, so a cleanup pass need not check first.
        """
        ...

    async def exists(self, key: str) -> bool:
        """Whether an object is stored at ``key``."""
        ...

    async def stat(self, key: str) -> ObjectStat | None:
        """Metadata for the object, or ``None`` if there is none.

        ``None`` rather than a raise: absence is the expected answer often
        enough that making callers catch would be noise. Contrast ``download``,
        where absence means the caller's assumption was wrong.
        """
        ...

    async def check(self) -> bool:
        """Readiness probe. ``False`` rather than raising, like the database
        and the queue, so a degraded state can be reported instead of a 500."""
        ...

    async def close(self) -> None:
        """Release connections held for the life of the process."""
        ...
