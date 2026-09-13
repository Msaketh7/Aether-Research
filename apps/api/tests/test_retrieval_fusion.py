"""Reciprocal Rank Fusion: what it does with two rankings that disagree.

No database here. Fusion is arithmetic over orderings, and the properties worth
asserting - that scale cannot influence the outcome, that a weight of zero
removes an arm rather than neutralising it, that the same input always produces
the same order - are all visible without one.
"""

from __future__ import annotations

import pytest

from app.retrieval.fusion import DEFAULT_RRF_K, reciprocal_rank_fusion
from app.retrieval.results import RetrievalStrategy, ScoredChunk
from tests.support.retrieval import chunk_id, scored

DENSE = RetrievalStrategy.DENSE
LEXICAL = RetrievalStrategy.LEXICAL


def ranking(*names: str, score: float = 1.0) -> list[ScoredChunk]:
    return [scored(name, score) for name in names]


def names(fused) -> list[str]:
    by_id = {chunk_id(name): name for name in ("a", "b", "c", "d", "x", "x2", "y", "y2")}
    return [by_id[item.hit.chunk.id] for item in fused]


def test_a_chunk_both_arms_rank_beats_one_each_ranks_higher():
    """The property RRF exists for: agreement outweighs a single strong opinion."""
    fused = reciprocal_rank_fusion(
        {DENSE: ranking("a", "b"), LEXICAL: ranking("c", "b")},
    )
    assert names(fused)[0] == "b"


def test_scores_cannot_influence_the_outcome():
    """Only order is read, which is what makes two incomparable scales fusable.

    The dense arm's scores here are a thousand times the lexical arm's. If any
    of them reached the fused score, the dense ranking would dominate.
    """
    modest = reciprocal_rank_fusion(
        {DENSE: ranking("a", "b", score=0.4), LEXICAL: ranking("c", "d", score=0.4)}
    )
    lopsided = reciprocal_rank_fusion(
        {DENSE: ranking("a", "b", score=400.0), LEXICAL: ranking("c", "d", score=0.4)}
    )
    assert names(modest) == names(lopsided)


def test_each_arms_rank_and_score_survive_the_fusion():
    """Fusion must not destroy the working: a per-arm rank is how a retrieval bug shows."""
    fused = reciprocal_rank_fusion(
        {
            DENSE: [scored("a", 0.91)],
            LEXICAL: [scored("b", 0.2), scored("a", 0.1)],
        }
    )
    first = next(item for item in fused if item.hit.chunk.id == chunk_id("a"))
    assert first.ranks == {DENSE: 1, LEXICAL: 2}
    assert first.scores == {DENSE: 0.91, LEXICAL: 0.1}


def test_a_zero_weight_removes_an_arm_rather_than_scoring_it_zero():
    """`found_by` has to stay honest: a disabled arm found nothing, it did not lose."""
    fused = reciprocal_rank_fusion(
        {DENSE: ranking("a"), LEXICAL: ranking("b")},
        weights={DENSE: 0.0, LEXICAL: 1.0},
    )
    assert names(fused) == ["b"]
    assert fused[0].ranks == {LEXICAL: 1}


def test_weights_shift_the_order_without_removing_anything():
    fused = reciprocal_rank_fusion(
        {DENSE: ranking("a", "b"), LEXICAL: ranking("b", "a")},
        weights={DENSE: 5.0, LEXICAL: 1.0},
    )
    assert names(fused) == ["a", "b"]


def test_k_controls_how_much_the_top_rank_is_worth():
    """Small k favours a single first place; large k favours agreement."""
    rankings = {DENSE: ranking("a", "x", "x2", "b"), LEXICAL: ranking("b", "y", "y2", "a")}
    # Symmetric input: with a symmetric k the two tie and the id breaks it, so
    # the assertion is about the scores, not the order.
    tight = reciprocal_rank_fusion(rankings, k=1)
    wide = reciprocal_rank_fusion(rankings, k=1000)
    spread = max(item.score for item in tight) - min(item.score for item in tight)
    flat = max(item.score for item in wide) - min(item.score for item in wide)
    assert spread > flat


def test_the_same_input_always_fuses_to_the_same_order():
    """Ties break on chunk id. A benchmark whose ranking drifts measures noise.

    Every chunk here scores identically - each is first in one arm and last in
    the other - so the order is decided entirely by the tiebreak.
    """
    rankings = {DENSE: ranking("a", "b", "c"), LEXICAL: ranking("c", "b", "a")}
    orders = {tuple(names(reciprocal_rank_fusion(rankings))) for _ in range(10)}
    assert len(orders) == 1


def test_one_arm_alone_keeps_its_own_order():
    """Single-arm retrieval goes through the same fusion, and must not reorder."""
    fused = reciprocal_rank_fusion({LEXICAL: ranking("a", "b", "c")})
    assert names(fused) == ["a", "b", "c"]


def test_no_rankings_at_all_fuses_to_nothing():
    assert reciprocal_rank_fusion({}) == []


def test_k_must_be_positive():
    """k=0 would divide by zero at rank 0; there is no sensible fallback."""
    with pytest.raises(ValueError, match="at least 1"):
        reciprocal_rank_fusion({DENSE: ranking("a")}, k=0)


def test_the_default_k_is_the_published_one():
    """Documented rather than invented: 60, from Cormack, Clarke and Buettcher."""
    assert DEFAULT_RRF_K == 60
