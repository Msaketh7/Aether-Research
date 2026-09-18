"""Object storage: the warehouse beside the system of record (ADR 0010).

Postgres holds rows; this holds bytes. Anything bulky - a PDF, the raw HTML of a
fetched page, a normalised document, a screenshot, a generated report, a
benchmark artifact - is written here and referenced from a row by its key
(TDD 7.1). Nothing large goes in the database.

Import ``ObjectStorage`` for the type and ``build_object_storage`` to get one.
Nothing outside this package should import ``S3ObjectStorage`` directly; that is
what makes the provider replaceable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.config import Settings
from app.core.logging import get_logger
from app.storage.base import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CONTENT_TYPE,
    ObjectStat,
    ObjectStorage,
    StoredObject,
)
from app.storage.errors import (
    InvalidStorageKey,
    ObjectNotFound,
    ObjectTooLarge,
    StorageError,
    StorageUnavailable,
)
from app.storage.filesystem import FilesystemObjectStorage
from app.storage.keys import (
    DEFAULT_CONTENT_TYPES,
    EXTENSIONS,
    ArtifactKind,
    content_addressed_name,
    content_digest,
    evaluation_artifact_key,
    kind_prefix,
    run_artifact_key,
    run_prefix,
    upload_artifact_key,
    user_uploads_prefix,
    validate_key,
)

if TYPE_CHECKING:
    from app.storage.s3 import S3ObjectStorage

logger = get_logger(__name__)


def __getattr__(name: str) -> Any:
    """Expose ``S3ObjectStorage`` without importing aioboto3 to do it (PEP 562).

    The S3 backend pulls in aioboto3, and botocore's service model loading costs
    about 2.6 s of import time. Under ``APP_ENV=test`` the filesystem backend is
    the one selected, so a test process would pay that for a module it never
    constructs. The name stays importable from this package - it is in
    ``__all__``, and the S3 suite imports it from here - it just arrives on
    first use rather than on first import.
    """
    if name == "S3ObjectStorage":
        from app.storage.s3 import S3ObjectStorage

        return S3ObjectStorage
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def build_object_storage(settings: Settings) -> ObjectStorage:
    """Select a backend from configuration.

    S3 everywhere except tests, mirroring how ``_build_queue`` picks Redis. The
    filesystem backend is available in development for machines without Docker,
    but choosing it in production is a configuration error and is refused here
    rather than at the first write - when the artifacts of a real research run
    would already be on a disk that is about to be discarded.
    """
    backend = settings.resolved_storage_backend

    if backend == "filesystem":
        if settings.is_production:
            raise ValueError(
                "STORAGE_BACKEND=filesystem is not permitted in production: "
                "artifacts would not survive a deploy. Configure S3."
            )
        logger.info(
            "object storage: filesystem backend",
            extra={"root": str(settings.storage_local_path)},
        )
        return FilesystemObjectStorage(
            settings.storage_local_path,
            max_bytes=settings.max_artifact_bytes,
        )

    from app.storage.s3 import S3ObjectStorage

    return S3ObjectStorage(settings)


__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "DEFAULT_CONTENT_TYPE",
    "DEFAULT_CONTENT_TYPES",
    "EXTENSIONS",
    "ArtifactKind",
    "FilesystemObjectStorage",
    "InvalidStorageKey",
    "ObjectNotFound",
    "ObjectStat",
    "ObjectStorage",
    "ObjectTooLarge",
    "S3ObjectStorage",
    "StorageError",
    "StorageUnavailable",
    "StoredObject",
    "build_object_storage",
    "content_addressed_name",
    "content_digest",
    "evaluation_artifact_key",
    "kind_prefix",
    "run_artifact_key",
    "run_prefix",
    "upload_artifact_key",
    "user_uploads_prefix",
    "validate_key",
]
