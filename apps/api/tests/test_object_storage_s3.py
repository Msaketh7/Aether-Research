"""S3-specific behaviour: failure translation, addressing, and backend selection.

The shared contract lives in ``test_object_storage.py``. What is asserted here is
the part that only matters against a real S3 endpoint - and it is asserted
against one, over HTTP.

The classification is the substance of these tests. "The bucket does not exist"
and "the endpoint is unreachable" produce the same ``ClientError`` shape from
botocore and completely different operational responses: one is a deploy that
must be fixed, the other is a retry. Getting that wrong makes a broken
configuration look like a flaky network for as long as it takes someone to read
the logs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.storage import (
    ArtifactKind,
    FilesystemObjectStorage,
    S3ObjectStorage,
    StorageError,
    StorageUnavailable,
    build_object_storage,
    run_artifact_key,
)
from tests.conftest import s3_settings
from tests.support.s3 import S3Server

KEY = run_artifact_key(run_id=uuid4(), kind=ArtifactKind.DOCUMENT, name="artifact.bin")


@pytest.fixture
async def missing_bucket_storage(s3_server: S3Server) -> AsyncIterator[S3ObjectStorage]:
    """A client pointed at a bucket that was never created."""
    storage = S3ObjectStorage(s3_settings(s3_server, bucket=f"absent-{uuid4().hex[:12]}"))
    try:
        yield storage
    finally:
        await storage.close()


@pytest.fixture
async def unreachable_storage() -> AsyncIterator[S3ObjectStorage]:
    """A client pointed at a port with nothing listening.

    Port 1 is reserved and closed, so the connection is refused immediately -
    the test asserts a classification, not a timeout, and must not spend the
    timeout to do it.
    """
    storage = S3ObjectStorage(
        Settings(
            app_env="test",
            log_level="warning",
            storage_backend="s3",
            s3_endpoint_url="http://127.0.0.1:1",
            s3_bucket="aether-artifacts",
            s3_access_key_id="unused",
            s3_secret_access_key="unused",  # noqa: S106 - nothing is listening
            s3_connect_timeout_seconds=1,
            s3_max_attempts=1,
        )
    )
    try:
        yield storage
    finally:
        await storage.close()


# --- failure classification -------------------------------------------------


async def test_an_unreachable_endpoint_is_retryable_not_an_internal_error(
    unreachable_storage: S3ObjectStorage,
):
    """503, the same class a Postgres or Redis outage produces, so the readiness
    probe and the frontend's retry logic need no special case for storage."""
    with pytest.raises(StorageUnavailable) as raised:
        await unreachable_storage.download(KEY)

    assert raised.value.status_code == 503
    assert raised.value.code == "storage_unavailable"


async def test_a_missing_bucket_is_reported_as_a_fault_not_a_blip(
    missing_bucket_storage: S3ObjectStorage,
):
    """Retrying a NoSuchBucket forever hides a broken deployment behind what
    looks like an intermittent failure."""
    with pytest.raises(StorageError) as raised:
        await missing_bucket_storage.upload(KEY, b"payload")

    assert raised.value.status_code == 500
    assert not isinstance(raised.value, StorageUnavailable)


async def test_readiness_fails_when_the_bucket_is_missing(
    missing_bucket_storage: S3ObjectStorage,
):
    """`check` HEADs the bucket, not the account. Credentials that work while
    every write fails must not report ready."""
    assert await missing_bucket_storage.check() is False


async def test_readiness_fails_when_the_endpoint_is_unreachable(
    unreachable_storage: S3ObjectStorage,
):
    assert await unreachable_storage.check() is False


async def test_a_failure_message_does_not_leak_the_endpoint_or_bucket(
    unreachable_storage: S3ObjectStorage,
):
    """Threat model 3.5: internal detail goes to the log under the request id,
    never to the client."""
    with pytest.raises(StorageUnavailable) as raised:
        await unreachable_storage.download(KEY)

    assert "127.0.0.1" not in raised.value.message
    assert "aether-artifacts" not in raised.value.message
    # It is still diagnosable - the detail is in the context the logger reads.
    assert raised.value.context["bucket"] == "aether-artifacts"


# --- bucket bootstrap -------------------------------------------------------


async def test_ensure_bucket_creates_then_tolerates_an_existing_bucket(
    s3_server: S3Server,
):
    """Local MinIO setup has to be safe to run twice."""
    storage = S3ObjectStorage(s3_settings(s3_server, bucket=f"twice-{uuid4().hex[:12]}"))
    try:
        await storage.ensure_bucket()
        await storage.ensure_bucket()

        assert await storage.check() is True
    finally:
        await storage.close()


async def test_ensure_bucket_works_outside_the_default_region(s3_server: S3Server):
    """S3 requires a LocationConstraint everywhere except us-east-1 and rejects
    one there, so a single call cannot serve both. A developer configuring
    eu-west-1 must not meet an IllegalLocationConstraint."""
    storage = S3ObjectStorage(
        s3_settings(s3_server, bucket=f"eu-{uuid4().hex[:12]}", s3_region="eu-west-1")
    )
    try:
        await storage.ensure_bucket()

        assert await storage.check() is True
    finally:
        await storage.close()


# --- addressing -------------------------------------------------------------


def test_a_custom_endpoint_gets_path_style_addressing(s3_server: S3Server):
    """`auto` would resolve a DNS-compatible bucket to a virtual host, turning
    http://localhost:9000 into http://aether-artifacts.localhost:9000, which
    does not resolve. This is the setting that makes MinIO work at all."""
    storage = S3ObjectStorage(s3_settings(s3_server, bucket="aether-artifacts"))

    assert storage._addressing_style == "path"


def test_real_s3_gets_virtual_host_addressing():
    """AWS is deprecating path-style addressing for new buckets."""
    storage = S3ObjectStorage(
        Settings(app_env="test", s3_endpoint_url=None, s3_bucket="aether-artifacts")
    )

    assert storage._addressing_style == "virtual"


def test_an_explicit_addressing_style_overrides_the_default():
    storage = S3ObjectStorage(
        Settings(app_env="test", s3_endpoint_url=None, s3_addressing_style="path")
    )

    assert storage._addressing_style == "path"


# --- backend selection ------------------------------------------------------


def test_tests_get_the_filesystem_backend_without_asking(tmp_path: Path):
    """So the suite and a Docker-less machine work; the same rule the in-memory
    queue follows."""
    settings = Settings(app_env="test", storage_local_path=tmp_path)

    assert isinstance(build_object_storage(settings), FilesystemObjectStorage)


def test_every_other_environment_gets_s3_by_default():
    """The fallback direction matters: omitting configuration must never
    silently downgrade a real deployment to local disk."""
    from joserfc.jwk import ECKey

    # `staging` needs a signing key like any other real deployment (ADR 0022);
    # it is nothing to do with storage, but a Settings cannot be built without.
    staging = Settings(
        app_env="staging",
        jwt_private_key=ECKey.generate_key("P-256").as_pem(private=True).decode(),
    )

    assert isinstance(build_object_storage(Settings(app_env="local")), S3ObjectStorage)
    assert isinstance(build_object_storage(staging), S3ObjectStorage)


def test_explicit_configuration_wins_over_the_environment_default(tmp_path: Path):
    settings = Settings(app_env="local", storage_backend="filesystem", storage_local_path=tmp_path)

    assert isinstance(build_object_storage(settings), FilesystemObjectStorage)


def test_the_filesystem_backend_is_refused_in_production(tmp_path: Path):
    """Artifacts on a container's disk do not survive the next deploy, and the
    run that wrote them would have citations pointing at nothing."""
    from joserfc.jwk import ECKey

    settings = Settings(
        app_env="production",
        # Unrelated to storage, but a production Settings is invalid without a
        # signing key since ADR 0022.
        jwt_private_key=ECKey.generate_key("P-256").as_pem(private=True).decode(),
        storage_backend="filesystem",
        storage_local_path=tmp_path,
    )

    with pytest.raises(ValueError, match="production"):
        build_object_storage(settings)
