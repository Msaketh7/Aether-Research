"""S3-compatible object storage.

One implementation serves both targets. MinIO in local development and AWS S3 in
production speak the same protocol, so the difference is configuration - an
endpoint URL and an addressing style - not code. That is the point of choosing an
S3-compatible store rather than a proprietary API (ADR 0010).

Three things this class does that a bare ``boto3`` call site would not:

* **Bounds every call.** Connect and read timeouts, a bounded retry policy, and
  a size ceiling on both directions. An unbounded S3 read inside a worker is a
  hung research run that never reports why.
* **Translates every failure.** No ``ClientError`` escapes. A missing key, an
  unreachable endpoint and a denied credential are three different errors with
  three different retry semantics, and the caller sees that distinction.
* **Records every call.** Operation, key, byte count, duration and outcome, on
  one structured line - the same treatment the project rules require for tool
  and model calls, because storage is on the critical path of a run and an
  unexplained 40-second stall has to be attributable.

Credentials are optional by design. When they are unset, botocore's default
chain resolves them - which on ECS means the task role (ADR 0008), so production
holds no long-lived S3 keys at all.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Literal, NoReturn

import aioboto3
from aiobotocore.config import AioConfig
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.storage.base import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CONTENT_TYPE,
    ObjectStat,
    StoredObject,
)
from app.storage.errors import (
    ObjectNotFound,
    ObjectTooLarge,
    StorageError,
    StorageUnavailable,
)
from app.storage.keys import validate_key

if TYPE_CHECKING:  # pragma: no cover - typing only
    from types_aiobotocore_s3.client import S3Client

logger = get_logger(__name__)

#: Error codes S3 and MinIO use for "no such object". S3 returns ``NoSuchKey``
#: on GET and a bare ``404`` on HEAD, because HEAD has no body to put a code in.
_NOT_FOUND_CODES = frozenset({"NoSuchKey", "NotFound", "404"})

#: Codes that mean the deployment is wrong, not that the network hiccuped.
#: Retrying any of these just delays the page.
_CONFIGURATION_CODES = frozenset(
    {
        "NoSuchBucket",
        "AccessDenied",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "AllAccessDisabled",
        "AuthorizationHeaderMalformed",
        "PermanentRedirect",
    }
)


class S3ObjectStorage:
    """``ObjectStorage`` backed by S3 or any S3-compatible endpoint."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bucket = settings.s3_bucket
        self._max_bytes = settings.max_artifact_bytes
        self._stack: AsyncExitStack | None = None
        self._client: S3Client | None = None
        # The client is built on first use from possibly-concurrent coroutines;
        # without this two of them race and one leaks an aiohttp session.
        self._lock = asyncio.Lock()

    # --- connection ------------------------------------------------------

    @property
    def _addressing_style(self) -> Literal["path", "virtual"]:
        """Resolve ``auto`` to something deterministic.

        botocore's ``auto`` prefers virtual-host addressing when the bucket name
        is DNS-compatible, which turns ``http://localhost:9000`` into
        ``http://aether-artifacts.localhost:9000`` and fails to resolve. Any
        custom endpoint therefore gets path style unless it is overridden;
        real S3 gets virtual-host style, which is what AWS wants.
        """
        configured = self._settings.s3_addressing_style
        if configured != "auto":
            return configured
        return "path" if self._settings.s3_endpoint_url else "virtual"

    async def _get_client(self) -> S3Client:
        """Build the client on first use.

        Lazily, for the same reason the database engine is lazy: the process
        must start, serve ``/health`` and report an honest ``/ready`` while the
        object store is down, rather than crash-loop before it can say why.

        The lock is taken on every call rather than guarding a fast path: an
        uncontended ``asyncio.Lock`` costs far less than the S3 round trip that
        follows, and double-checked locking here would only add a way to get it
        wrong.
        """
        async with self._lock:
            if self._client is not None:
                return self._client

            settings = self._settings
            config = AioConfig(
                connect_timeout=settings.s3_connect_timeout_seconds,
                read_timeout=settings.s3_read_timeout_seconds,
                # "standard" mode retries throttling and transient 5xx with
                # exponential backoff and honours the attempt ceiling; the
                # legacy mode does neither reliably.
                retries={"max_attempts": settings.s3_max_attempts, "mode": "standard"},
                s3={"addressing_style": self._addressing_style},
            )
            session = aioboto3.Session(
                # None lets botocore's chain resolve them: environment, shared
                # config, then the ECS task role in production.
                aws_access_key_id=_secret(settings.s3_access_key_id),
                aws_secret_access_key=_secret(settings.s3_secret_access_key),
                region_name=settings.s3_region,
            )

            stack = AsyncExitStack()
            try:
                client = await stack.enter_async_context(
                    session.client(
                        "s3",
                        endpoint_url=settings.s3_endpoint_url,
                        config=config,
                    )
                )
            except Exception:
                await stack.aclose()
                raise

            self._stack = stack
            self._client = client
            logger.info(
                "object storage client created",
                extra={
                    "bucket": self._bucket,
                    "endpoint": settings.s3_endpoint_url or "aws",
                    "addressing_style": self._addressing_style,
                },
            )
            return client

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
            self._client = None

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
        self._require_within_ceiling(key, len(data), operation="upload")

        client = await self._get_client()
        started = time.perf_counter()
        try:
            response = await client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                Metadata=dict(metadata or {}),
            )
        except Exception as exc:
            _fail(exc, key=key, operation="upload", bucket=self._bucket)

        self._log_ok("upload", key, started, size_bytes=len(data))
        return StoredObject(
            key=key,
            size_bytes=len(data),
            content_type=content_type,
            etag=_clean_etag(response.get("ETag")),
        )

    async def download(self, key: str) -> bytes:
        validate_key(key)
        client = await self._get_client()
        started = time.perf_counter()
        try:
            response = await client.get_object(Bucket=self._bucket, Key=key)
            # Check the advertised length before reading, so an oversized object
            # is refused rather than pulled into memory and then rejected.
            self._require_within_ceiling(
                key, int(response.get("ContentLength", 0)), operation="download"
            )
            # Bind the body *before* the `async with`: aiobotocore's
            # StreamingBody is a proxy whose __aenter__ returns the wrapped
            # aiohttp response, and the wrapped object has none of the
            # botocore stream API. Entering it for the connection release
            # while reading through the proxy is what keeps both.
            body = response["Body"]
            async with body:
                data: bytes = await body.read()
        except Exception as exc:
            _fail(exc, key=key, operation="download", bucket=self._bucket)

        self._log_ok("download", key, started, size_bytes=len(data))
        return data

    async def download_stream(
        self,
        key: str,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> AsyncIterator[bytes]:
        validate_key(key)
        client = await self._get_client()
        started = time.perf_counter()
        total = 0
        try:
            response = await client.get_object(Bucket=self._bucket, Key=key)
            body = response["Body"]
            async with body:
                # `iter_chunks`, not `read(n)`: see the note in `download`.
                async for chunk in body.iter_chunks(chunk_size):
                    total += len(chunk)
                    yield chunk
        except Exception as exc:
            _fail(exc, key=key, operation="download_stream", bucket=self._bucket)

        self._log_ok("download_stream", key, started, size_bytes=total)

    async def delete(self, key: str) -> bool:
        validate_key(key)
        # S3's DELETE is unconditionally successful, so "was it there?" needs a
        # HEAD first. Two round trips, but a cleanup pass that cannot tell what
        # it actually removed cannot report or meter anything.
        existed = await self.exists(key)

        client = await self._get_client()
        started = time.perf_counter()
        try:
            await client.delete_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            _fail(exc, key=key, operation="delete", bucket=self._bucket)

        self._log_ok("delete", key, started, existed=existed)
        return existed

    async def exists(self, key: str) -> bool:
        return await self.stat(key) is not None

    async def stat(self, key: str) -> ObjectStat | None:
        validate_key(key)
        client = await self._get_client()
        try:
            head = await client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in _NOT_FOUND_CODES:
                return None
            _fail(exc, key=key, operation="stat", bucket=self._bucket)
        except Exception as exc:
            _fail(exc, key=key, operation="stat", bucket=self._bucket)

        return ObjectStat(
            key=key,
            size_bytes=int(head.get("ContentLength", 0)),
            content_type=str(head.get("ContentType", DEFAULT_CONTENT_TYPE)),
            etag=_clean_etag(head.get("ETag")),
            last_modified=head.get("LastModified"),
            metadata=dict(head.get("Metadata", {})),
        )

    async def check(self) -> bool:
        """Can this process reach *its own bucket*?

        A HEAD on the bucket rather than a ``list_buckets``: the latter succeeds
        on credentials alone and would report ready while every write fails with
        NoSuchBucket. Readiness has to test the thing that is actually used.
        """
        try:
            client = await self._get_client()
            async with asyncio.timeout(self._settings.s3_connect_timeout_seconds + 2):
                await client.head_bucket(Bucket=self._bucket)
        except Exception as exc:
            logger.warning(
                "object storage health check failed",
                extra={"bucket": self._bucket, "error": str(exc)},
            )
            return False
        return True

    async def ensure_bucket(self) -> None:
        """Create the bucket if it is missing.

        Only for local MinIO and tests. It is never called during startup: in
        production the bucket is Terraform-managed with versioning, encryption
        and lifecycle rules, and a service that creates its own would quietly
        produce one with none of those.
        """
        client = await self._get_client()
        try:
            await client.head_bucket(Bucket=self._bucket)
            return
        except ClientError as exc:
            if _error_code(exc) not in _NOT_FOUND_CODES | {"NoSuchBucket"}:
                _fail(exc, key="", operation="ensure_bucket", bucket=self._bucket)
        except Exception as exc:
            _fail(exc, key="", operation="ensure_bucket", bucket=self._bucket)

        region = self._settings.s3_region
        try:
            if region == "us-east-1":
                # S3 rejects an explicit LocationConstraint for its default
                # region, and requires one for every other. There is no form
                # that works for both.
                await client.create_bucket(Bucket=self._bucket)
            else:
                await client.create_bucket(
                    Bucket=self._bucket,
                    CreateBucketConfiguration={"LocationConstraint": region},  # type: ignore[typeddict-item]
                )
        except ClientError as exc:
            # A concurrent creator won; that is the desired end state either way.
            if _error_code(exc) not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                _fail(exc, key="", operation="ensure_bucket", bucket=self._bucket)
            return
        logger.info("artifact bucket created", extra={"bucket": self._bucket, "region": region})

    # --- internals -------------------------------------------------------

    def _require_within_ceiling(self, key: str, size_bytes: int, *, operation: str) -> None:
        if size_bytes > self._max_bytes:
            raise ObjectTooLarge(
                f"That artifact is larger than the {self._max_bytes} byte ceiling.",
                context={
                    "key": key,
                    "operation": operation,
                    "size_bytes": size_bytes,
                    "max_bytes": self._max_bytes,
                },
            )

    def _log_ok(self, operation: str, key: str, started: float, **fields: object) -> None:
        logger.info(
            "object storage call",
            extra={
                "operation": operation,
                "key": key,
                "bucket": self._bucket,
                "status": "ok",
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                **fields,
            },
        )


def _secret(value: SecretStr | None) -> str | None:
    """Unwrap a ``SecretStr`` without turning ``None`` into ``"None"``."""
    return None if value is None else str(value.get_secret_value())


def _clean_etag(raw: str | None) -> str:
    """S3 returns the ETag wrapped in literal quote characters."""
    return (raw or "").strip('"')


def _error_code(exc: ClientError) -> str:
    error = exc.response.get("Error", {})
    code = str(error.get("Code", ""))
    if code:
        return code
    status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return str(status) if status is not None else ""


def _fail(exc: Exception, *, key: str, operation: str, bucket: str) -> NoReturn:
    """Translate a provider exception into the storage taxonomy.

    Every raise carries the internal detail in ``context`` - which goes to the
    log - and never in the message, which goes to the user. An endpoint URL or a
    bucket name in an error body is an information disclosure the threat model
    calls out (section 3.5).
    """
    # A storage error raised *inside* the guarded block - an oversized object,
    # an invalid key - is already correctly classified. Re-translating it would
    # turn a 413 into a 500.
    if isinstance(exc, AppError):
        raise exc

    context = {"key": key, "operation": operation, "bucket": bucket, "error": str(exc)}

    if isinstance(exc, ClientError):
        code = _error_code(exc)
        status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
        context["s3_code"] = code

        if code in _NOT_FOUND_CODES:
            raise ObjectNotFound(context=context) from exc
        if code in _CONFIGURATION_CODES:
            logger.error("object storage misconfigured", extra=context)
            raise StorageError(context=context) from exc
        if status >= 500 or code in {"SlowDown", "RequestTimeout", "ServiceUnavailable"}:
            logger.warning("object storage transient failure", extra=context)
            raise StorageUnavailable(context=context) from exc

        logger.error("object storage request rejected", extra=context)
        raise StorageError(context=context) from exc

    if isinstance(exc, BotoCoreError | TimeoutError | OSError):
        # Connection refused, DNS failure, TLS failure, read timeout: the store
        # is unreachable right now and the caller should retry.
        logger.warning("object storage unreachable", extra=context)
        raise StorageUnavailable(context=context) from exc

    logger.error("object storage call failed", extra=context)
    raise StorageError(context=context) from exc
