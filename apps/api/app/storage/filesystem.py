"""Filesystem-backed object storage, for tests and for development without Docker.

The same role ``InMemoryJobQueue`` plays for Redis, and chosen under the same
rule: it is **never selected implicitly**. ``build_object_storage`` picks it only
for ``APP_ENV=test`` or an explicit ``STORAGE_BACKEND=filesystem``, and refuses
it outright in production. A misconfigured deployment must fail its readiness
probe, not silently write a user's research artifacts to a container's ephemeral
disk where the next deploy destroys them.

It exists because Docker is not available on every development machine, so
without it the entire storage path would be untestable and unrunnable locally -
and an interface with one implementation is not an interface, it is indirection.
Having a second one is what proves the abstraction does not leak S3 semantics.

Content type and user metadata have nowhere to live on a filesystem, so each
object gets a sidecar JSON file next to it. That is the honest cost of the
backend; the alternative - dropping the metadata - would make the two
implementations behave differently, which is precisely what a test double must
not do.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path

from app.core.logging import get_logger
from app.storage.base import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CONTENT_TYPE,
    ObjectStat,
    StoredObject,
)
from app.storage.errors import ObjectNotFound, ObjectTooLarge, StorageError
from app.storage.keys import validate_key

logger = get_logger(__name__)

#: Sidecar suffix. Chosen to be rejected by ``validate_key`` - a segment cannot
#: contain '#' - so no real object key can ever collide with a metadata file.
_SIDECAR_SUFFIX = "#meta.json"


class FilesystemObjectStorage:
    """``ObjectStorage`` over a directory tree."""

    def __init__(self, root: Path, *, max_bytes: int) -> None:
        self._root = root.resolve()
        self._max_bytes = max_bytes

    # --- operations ------------------------------------------------------

    async def upload(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str = DEFAULT_CONTENT_TYPE,
        metadata: Mapping[str, str] | None = None,
    ) -> StoredObject:
        validate_key(key)
        if len(data) > self._max_bytes:
            raise ObjectTooLarge(
                f"That artifact is larger than the {self._max_bytes} byte ceiling.",
                context={"key": key, "size_bytes": len(data), "max_bytes": self._max_bytes},
            )

        path = self._path_for(key)
        etag = hashlib.md5(data, usedforsecurity=False).hexdigest()
        sidecar = json.dumps(
            {
                "content_type": content_type,
                "metadata": dict(metadata or {}),
                "etag": etag,
            }
        )
        await asyncio.to_thread(self._write, path, data, sidecar)

        logger.info(
            "object storage call",
            extra={"operation": "upload", "key": key, "status": "ok", "size_bytes": len(data)},
        )
        return StoredObject(
            key=key,
            size_bytes=len(data),
            content_type=content_type,
            etag=etag,
        )

    async def download(self, key: str) -> bytes:
        validate_key(key)
        path = self._path_for(key)
        size = await asyncio.to_thread(self._size_or_none, path)
        if size is None:
            raise ObjectNotFound(context={"key": key})
        if size > self._max_bytes:
            raise ObjectTooLarge(
                f"That artifact is larger than the {self._max_bytes} byte ceiling.",
                context={"key": key, "size_bytes": size, "max_bytes": self._max_bytes},
            )
        return await asyncio.to_thread(path.read_bytes)

    async def download_stream(
        self,
        key: str,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> AsyncIterator[bytes]:
        validate_key(key)
        path = self._path_for(key)
        if await asyncio.to_thread(self._size_or_none, path) is None:
            raise ObjectNotFound(context={"key": key})

        handle = await asyncio.to_thread(path.open, "rb")
        try:
            while chunk := await asyncio.to_thread(handle.read, chunk_size):
                yield chunk
        finally:
            # Runs on exhaustion, on an exception, and on the consumer closing
            # the generator early. A leaked handle on Windows blocks the next
            # write to the same key.
            await asyncio.to_thread(handle.close)

    async def delete(self, key: str) -> bool:
        validate_key(key)
        return await asyncio.to_thread(self._unlink, self._path_for(key))

    async def exists(self, key: str) -> bool:
        return await self.stat(key) is not None

    async def stat(self, key: str) -> ObjectStat | None:
        validate_key(key)
        path = self._path_for(key)
        return await asyncio.to_thread(self._stat, key, path)

    async def check(self) -> bool:
        """Is the root writable?

        Existence is not enough: a read-only mount would pass an existence check
        and fail every upload, which is the failure readiness exists to catch.
        """
        try:
            await asyncio.to_thread(self._probe_writable)
        except OSError as exc:
            logger.warning(
                "object storage health check failed",
                extra={"root": str(self._root), "error": str(exc)},
            )
            return False
        return True

    async def close(self) -> None:
        """Nothing is held open; each call opens and closes its own handle."""
        return None

    # --- internals -------------------------------------------------------

    def _path_for(self, key: str) -> Path:
        """Map a validated key to a path inside the root.

        ``validate_key`` has already rejected traversal, but this re-checks the
        *resolved* path. Two independent barriers, because a containment check
        that trusts an earlier one has historically been the bug.
        """
        candidate = (self._root / key).resolve()
        if candidate != self._root and self._root not in candidate.parents:
            raise StorageError(
                "Refusing to address a path outside the artifact root.",
                context={"key": key, "resolved": str(candidate)},
            )
        return candidate

    def _sidecar_for(self, path: Path) -> Path:
        return path.with_name(path.name + _SIDECAR_SUFFIX)

    def _write(self, path: Path, data: bytes, sidecar: str) -> None:
        """Write atomically, so a crashed process cannot leave a torn object.

        A reader that opens a half-written PDF gets a parse error attributed to
        the source rather than to us, which is an expensive thing to debug.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{id(data):x}.tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        self._sidecar_for(path).write_text(sidecar, encoding="utf-8")

    def _size_or_none(self, path: Path) -> int | None:
        try:
            return path.stat().st_size
        except (OSError, ValueError):
            return None

    def _stat(self, key: str, path: Path) -> ObjectStat | None:
        try:
            info = path.stat()
        except (OSError, ValueError):
            return None
        if not path.is_file():
            return None

        content_type = DEFAULT_CONTENT_TYPE
        etag = ""
        metadata: dict[str, str] = {}
        try:
            sidecar = json.loads(self._sidecar_for(path).read_text(encoding="utf-8"))
            content_type = str(sidecar.get("content_type", DEFAULT_CONTENT_TYPE))
            etag = str(sidecar.get("etag", ""))
            metadata = {str(k): str(v) for k, v in dict(sidecar.get("metadata", {})).items()}
        except (OSError, ValueError):
            # An object written before its sidecar, or by hand. Report what is
            # knowable rather than claiming the object does not exist.
            pass

        return ObjectStat(
            key=key,
            size_bytes=info.st_size,
            content_type=content_type,
            etag=etag,
            last_modified=datetime.fromtimestamp(info.st_mtime, tz=UTC),
            metadata=metadata,
        )

    def _unlink(self, path: Path) -> bool:
        existed = path.is_file()
        path.unlink(missing_ok=True)
        self._sidecar_for(path).unlink(missing_ok=True)
        return existed

    def _probe_writable(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        probe = self._root / ".writable-probe"
        probe.write_bytes(b"")
        probe.unlink(missing_ok=True)
