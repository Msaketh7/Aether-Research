"""The artifact key namespace.

Two things are being protected. First, the *layout*: everything a run produced
must share one prefix, or deleting a user's data becomes a database join and
lifecycle rules become impossible. Second, the *validation*: the filesystem
backend turns a key into a path, so a key that escapes the namespace escapes the
directory.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.storage.errors import InvalidStorageKey
from app.storage.keys import (
    DEFAULT_CONTENT_TYPES,
    EXTENSIONS,
    MAX_KEY_BYTES,
    ArtifactKind,
    content_addressed_name,
    content_digest,
    evaluation_artifact_key,
    kind_prefix,
    run_artifact_key,
    run_prefix,
    validate_key,
)

RUN_ID = UUID("11111111-2222-3333-4444-555555555555")


def test_every_artifact_of_a_run_shares_one_prefix():
    """Deleting a run must be a prefix operation, not a query."""
    keys = [
        run_artifact_key(run_id=RUN_ID, kind=kind, name="artifact.bin")
        for kind in ArtifactKind
        if kind is not ArtifactKind.EVALUATION
    ]

    assert all(key.startswith(run_prefix(RUN_ID)) for key in keys)
    # And they are distinguishable within it, so a lifecycle rule can expire
    # raw HTML without touching reports.
    assert len(set(keys)) == len(keys)


def test_the_kind_appears_in_the_path():
    key = run_artifact_key(run_id=RUN_ID, kind=ArtifactKind.RAW_HTML, name="page.html")

    assert key == f"runs/{RUN_ID}/raw-html/page.html"
    assert key.startswith(kind_prefix(RUN_ID, ArtifactKind.RAW_HTML))


def test_evaluation_artifacts_are_not_forced_under_a_run():
    """A benchmark spans many runs, so it cannot be scoped to one."""
    evaluation_id = uuid4()

    key = evaluation_artifact_key(evaluation_id=evaluation_id, name="scores.json")

    assert key == f"evaluations/{evaluation_id}/scores.json"
    assert not key.startswith("runs/")


def test_the_same_bytes_always_produce_the_same_name():
    """Content addressing is what makes re-ingesting a source idempotent."""
    first = content_addressed_name(ArtifactKind.PDF, b"%PDF-1.7 filing")
    second = content_addressed_name(ArtifactKind.PDF, b"%PDF-1.7 filing")
    different = content_addressed_name(ArtifactKind.PDF, b"%PDF-1.7 other")

    assert first == second
    assert first != different


def test_the_digest_is_the_one_the_schema_stores():
    """`documents.content_hash` and the storage key must agree on identity."""
    import hashlib

    data = b"normalised document text"

    assert content_digest(data) == hashlib.sha256(data).hexdigest()
    assert content_addressed_name(ArtifactKind.DOCUMENT, data).startswith(content_digest(data))


def test_content_addressed_names_carry_the_kind_extension():
    """An operator browsing the bucket can tell what a digest is."""
    for kind in ArtifactKind:
        assert content_addressed_name(kind, b"x").endswith(EXTENSIONS[kind])


def test_every_kind_has_a_content_type():
    """A missing entry would silently serve HTML as octet-stream."""
    assert set(DEFAULT_CONTENT_TYPES) == set(ArtifactKind)
    assert set(EXTENSIONS) == set(ArtifactKind)


@pytest.mark.parametrize(
    "key",
    [
        "runs/../../etc/passwd",
        "..",
        "a/../b",
        "a/./b",
        "/absolute/key",
        "trailing/",
        "double//slash",
        "back\\slash",
        "with space",
        "control\nchar",
        "",
        "unicode/ünicode",
        ".hidden",
    ],
)
def test_unsafe_keys_are_refused(key: str):
    """The filesystem backend joins a key onto a path; traversal is a write
    outside the artifact root."""
    with pytest.raises(InvalidStorageKey):
        validate_key(key)


def test_an_over_long_key_is_refused_before_it_reaches_s3():
    """S3 caps a key at 1024 bytes; failing here says why, a signing error
    does not."""
    with pytest.raises(InvalidStorageKey):
        validate_key("a" * (MAX_KEY_BYTES + 1))


def test_a_derived_key_always_validates():
    """The builders and the validator must not disagree; if they did, a key
    the system generates could be one it refuses to read back."""
    for kind in ArtifactKind:
        key = run_artifact_key(
            run_id=RUN_ID,
            kind=kind,
            name=content_addressed_name(kind, b"payload"),
        )
        assert validate_key(key) == key


def test_a_name_containing_a_separator_is_refused():
    """Otherwise a caller could smuggle a path into the `name` argument."""
    with pytest.raises(InvalidStorageKey):
        run_artifact_key(run_id=RUN_ID, kind=ArtifactKind.PDF, name="../../escape.pdf")
