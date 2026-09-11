"""Ingestion settings refuse limits that contradict each other.

Each refused combination is a deployment that would start, accept work, and then
fail on it - so it is refused at startup instead.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_the_upload_ceiling_defaults_to_the_artifact_ceiling():
    assert Settings(app_env="test", max_artifact_bytes=4096).upload_limit_bytes == 4096


def test_an_upload_ceiling_above_the_artifact_ceiling_is_refused():
    """It would accept files it could not store."""
    with pytest.raises(ValidationError, match="MAX_UPLOAD_BYTES"):
        Settings(app_env="test", max_artifact_bytes=1024, max_upload_bytes=2048)


def test_an_overlap_as_large_as_the_chunk_is_refused():
    """The splitter would never advance."""
    with pytest.raises(ValidationError, match="CHUNK_OVERLAP_TOKENS"):
        Settings(app_env="test", chunk_size_tokens=128, chunk_overlap_tokens=128)


@pytest.mark.parametrize(
    "field",
    [
        "max_pdf_pages",
        "max_document_chars",
        "max_chunks_per_document",
        "embedding_batch_size",
        "max_concurrent_parses",
    ],
)
def test_ingestion_bounds_must_be_positive(field):
    with pytest.raises(ValidationError):
        Settings(app_env="test", **{field: 0})
