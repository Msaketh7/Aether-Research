"""Ingestion and retrieval settings refuse limits that contradict each other.

Each refused combination is a deployment that would start, accept work, and then
fail on it - so it is refused at startup instead.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.retrieval.factory import build_retrieval_plan
from app.retrieval.query import RetrievalPlan


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


# --- retrieval (Phase 8) ----------------------------------------------------


def test_fewer_candidates_than_results_is_refused():
    """Fusion can only rank what the arms fetched, so such a deployment would
    return short of its own configured limit on every single call."""
    with pytest.raises(ValidationError, match="RETRIEVAL_CANDIDATES"):
        Settings(app_env="test", retrieval_limit=20, retrieval_candidates=10)


def test_both_arms_weighted_to_zero_is_refused():
    """It would search nothing at all, and report two skipped arms for every query."""
    with pytest.raises(ValidationError, match="cannot both be zero"):
        Settings(app_env="test", retrieval_dense_weight=0, retrieval_lexical_weight=0)


def test_one_arm_weighted_to_zero_is_allowed():
    """A deployment with no embeddings, or with a corpus the index cannot stem,
    turns an arm off deliberately. That is configuration, not a mistake."""
    assert Settings(app_env="test", retrieval_dense_weight=0).retrieval_lexical_weight == 1.0


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_an_mmr_lambda_outside_zero_to_one_is_refused(value):
    """Below zero inverts relevance: it would rank the worst candidate first."""
    with pytest.raises(ValidationError, match="RETRIEVAL_MMR_LAMBDA"):
        Settings(app_env="test", retrieval_mmr_lambda=value)


def test_the_settings_and_the_plan_agree_on_what_is_valid():
    """`RetrievalPlan` is the authority on these bounds and `Settings` restates
    them, because settings is a leaf that the retrieval layer reads. This is the
    assertion that stops the restatement drifting: every default the settings
    carry must build a plan, and the factory is how they meet."""
    settings = Settings(app_env="test")
    plan = build_retrieval_plan(settings)
    assert (plan.limit, plan.candidates, plan.rrf_k) == (
        settings.retrieval_limit,
        settings.retrieval_candidates,
        settings.retrieval_rrf_k,
    )
    assert (plan.dense_weight, plan.lexical_weight) == (
        settings.retrieval_dense_weight,
        settings.retrieval_lexical_weight,
    )


def test_a_configuration_the_plan_refuses_is_refused_at_startup_too():
    """The drift that would matter: settings accepting something the plan would
    reject means a deployment that starts and then fails on its first search."""
    with pytest.raises(ValidationError):
        Settings(app_env="test", retrieval_limit=0)
    with pytest.raises(ValueError):
        RetrievalPlan(limit=0)
