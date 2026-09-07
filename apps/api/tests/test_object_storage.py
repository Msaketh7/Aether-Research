"""The ``ObjectStorage`` contract, run against both implementations.

Every test here is parametrised over the S3 backend and the filesystem backend,
and the S3 one talks HTTP to a real S3 server. That is the point: an interface
with one implementation is indirection, and a contract only one implementation is
held to is a description of that implementation. If the filesystem backend is
ever used to make a test pass that S3 would fail, this file fails.

Behaviour that is genuinely S3-specific - error translation, addressing style,
bucket bootstrap - lives in ``test_object_storage_s3.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest

from app.storage import (
    ArtifactKind,
    FilesystemObjectStorage,
    ObjectNotFound,
    ObjectStorage,
    ObjectTooLarge,
    S3ObjectStorage,
    content_addressed_name,
    run_artifact_key,
)
from app.storage.errors import InvalidStorageKey
from tests.conftest import s3_settings
from tests.support.s3 import S3Server

#: A small ceiling, so the bound is exercised without allocating 25 MiB.
CEILING = 4096


@pytest.fixture(params=["filesystem", "s3"])
async def storage(
    request: pytest.FixtureRequest,
    tmp_path: Path,
    s3_server: S3Server,
) -> AsyncIterator[ObjectStorage]:
    if request.param == "filesystem":
        yield FilesystemObjectStorage(tmp_path / "objects", max_bytes=CEILING)
        return

    # A fresh bucket per test, so one test's leftovers cannot make another pass.
    backend = S3ObjectStorage(
        s3_settings(s3_server, bucket=f"test-{uuid4().hex[:12]}", max_artifact_bytes=CEILING)
    )
    await backend.ensure_bucket()
    try:
        yield backend
    finally:
        await backend.close()


def a_key(name: str = "artifact.bin") -> str:
    return run_artifact_key(run_id=uuid4(), kind=ArtifactKind.DOCUMENT, name=name)


# --- the four required operations ------------------------------------------


async def test_uploaded_bytes_come_back_unchanged(storage: ObjectStorage):
    key = a_key()
    payload = bytes(range(256)) * 3  # binary, not text: no encoding round trip

    receipt = await storage.upload(key, payload, content_type="application/octet-stream")
    assert await storage.download(key) == payload

    assert receipt.key == key
    assert receipt.size_bytes == len(payload)
    assert receipt.etag


async def test_exists_distinguishes_stored_from_absent(storage: ObjectStorage):
    key = a_key()

    assert await storage.exists(key) is False
    await storage.upload(key, b"payload")
    assert await storage.exists(key) is True


async def test_delete_reports_whether_there_was_anything_to_delete(storage: ObjectStorage):
    """A cleanup pass that cannot tell what it removed cannot meter anything."""
    key = a_key()
    await storage.upload(key, b"payload")

    assert await storage.delete(key) is True
    assert await storage.exists(key) is False
    # Idempotent: deleting again succeeds and reports the absence.
    assert await storage.delete(key) is False


async def test_downloading_a_missing_object_raises_not_found(storage: ObjectStorage):
    """`ObjectNotFound` is a 404, not a 500: an artifact a run never produced
    is an expected state, not a fault."""
    with pytest.raises(ObjectNotFound):
        await storage.download(a_key("never-written.bin"))


# --- metadata ---------------------------------------------------------------


async def test_stat_reports_size_and_content_type_without_reading(storage: ObjectStorage):
    key = a_key("page.html")
    await storage.upload(key, b"<html></html>", content_type="text/html; charset=utf-8")

    stat = await storage.stat(key)

    assert stat is not None
    assert stat.key == key
    assert stat.size_bytes == 13
    assert stat.content_type == "text/html; charset=utf-8"
    assert stat.etag


async def test_stat_returns_none_rather_than_raising_for_an_absent_object(
    storage: ObjectStorage,
):
    """Absence is the expected answer often enough that a raise would be noise."""
    assert await storage.stat(a_key()) is None


async def test_user_metadata_survives_a_round_trip(storage: ObjectStorage):
    """The provenance a later phase attaches to an artifact must not be dropped
    by whichever backend happens to be configured."""
    key = a_key()
    await storage.upload(key, b"payload", metadata={"source-id": "abc123"})

    stat = await storage.stat(key)

    assert stat is not None
    assert stat.metadata["source-id"] == "abc123"


# --- overwrite and idempotency ---------------------------------------------


async def test_uploading_the_same_key_twice_replaces_the_object(storage: ObjectStorage):
    """Keys are content-addressed or row-scoped, so a repeat write is a retry.
    Failing it would make ingestion non-idempotent for no benefit."""
    key = a_key()

    await storage.upload(key, b"first")
    await storage.upload(key, b"second version, longer")

    assert await storage.download(key) == b"second version, longer"
    stat = await storage.stat(key)
    assert stat is not None
    assert stat.size_bytes == len(b"second version, longer")


async def test_content_addressed_uploads_converge_on_one_object(storage: ObjectStorage):
    """Re-fetching the same PDF must not fill the bucket with duplicates."""
    run_id = uuid4()
    data = b"%PDF-1.7 the same filing"
    key = run_artifact_key(
        run_id=run_id,
        kind=ArtifactKind.PDF,
        name=content_addressed_name(ArtifactKind.PDF, data),
    )

    first = await storage.upload(key, data)
    second = await storage.upload(key, data)

    assert first.key == second.key
    assert first.etag == second.etag


# --- bounds -----------------------------------------------------------------


async def test_an_oversized_upload_is_refused(storage: ObjectStorage):
    with pytest.raises(ObjectTooLarge):
        await storage.upload(a_key(), b"x" * (CEILING + 1))


async def test_an_object_at_the_ceiling_is_accepted(storage: ObjectStorage):
    """The bound is a ceiling, not an off-by-one that rejects a legal object."""
    key = a_key()
    await storage.upload(key, b"x" * CEILING)

    assert await storage.exists(key) is True


async def test_an_unsafe_key_is_refused_before_any_call(storage: ObjectStorage):
    with pytest.raises(InvalidStorageKey):
        await storage.upload("../escape", b"payload")
    with pytest.raises(InvalidStorageKey):
        await storage.download("../escape")


# --- streaming --------------------------------------------------------------


async def test_streaming_yields_the_same_bytes_in_chunks(storage: ObjectStorage):
    """The escape hatch from the in-memory ceiling: a large artifact must be
    readable without being buffered whole."""
    key = a_key()
    payload = bytes(range(256)) * 8  # 2048 bytes
    await storage.upload(key, payload)

    chunks = [chunk async for chunk in storage.download_stream(key, chunk_size=512)]

    assert b"".join(chunks) == payload
    assert len(chunks) > 1, "chunk_size was ignored; nothing is actually streamed"


async def test_streaming_a_missing_object_raises_not_found(storage: ObjectStorage):
    with pytest.raises(ObjectNotFound):
        async for _ in storage.download_stream(a_key()):
            pass


async def test_a_partially_consumed_stream_can_be_abandoned(storage: ObjectStorage):
    """A caller that stops early - a cancelled run, a client disconnect - must
    not leave the connection or the file handle held."""
    key = a_key()
    await storage.upload(key, b"x" * 2048)

    stream = storage.download_stream(key, chunk_size=256)
    assert len(await anext(stream)) == 256
    await stream.aclose()

    # The store is still usable afterwards, which is what a leak would break.
    assert await storage.download(key) == b"x" * 2048


# --- readiness --------------------------------------------------------------


async def test_check_passes_against_a_reachable_store(storage: ObjectStorage):
    assert await storage.check() is True


# --- the interface itself ---------------------------------------------------


def test_both_backends_satisfy_the_protocol(storage: ObjectStorage):
    """Structural conformance, checked at runtime.

    ``mypy`` only sees this where a backend is passed to something annotated
    ``ObjectStorage``; a method renamed in one implementation and not the other
    would otherwise surface as an AttributeError in whichever environment
    happens to select it.
    """
    assert isinstance(storage, ObjectStorage)
