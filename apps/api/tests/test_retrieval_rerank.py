"""Reranking: what MMR does to a candidate list full of near-duplicates.

The case this exists for is concrete. Chunks overlap by 64 tokens, filings
restate their own risk factors, and a wire story gets reposted. A fused top-10
can be one fact ten times, which scores well on every ranking metric and starves
the report of everything else.
"""

from __future__ import annotations

import pytest

from app.retrieval.rerank import MaximalMarginalRelevance, NoReranker
from tests.support.retrieval import retrieved

REVENUE = "Nvidia reported data centre revenue of 26.3 billion dollars for the quarter."
REVENUE_AGAIN = "Nvidia reported data centre revenue of 26.3 billion dollars for the quarter, up."
EXPORTS = "Export controls would reduce shipments of accelerators to some regions, it said."
MEMORY = "Memory bandwidth, not raw compute, limits most production serving deployments today."


def names(ranked) -> list[str]:
    from tests.support.retrieval import chunk_id

    lookup = {chunk_id(name): name for name in ("a", "b", "c", "d", "e")}
    return [lookup[hit.chunk.id] for hit in ranked]


# --- no reranking ---------------------------------------------------------


async def test_no_reranker_keeps_the_fused_order_and_only_truncates():
    """The control arm of the benchmark. It must not reorder anything."""
    candidates = [retrieved("a", 0.9), retrieved("b", 0.8), retrieved("c", 0.7)]
    assert names(await NoReranker().rerank("q", candidates, limit=2)) == ["a", "b"]


async def test_no_reranker_reports_itself_as_none():
    assert NoReranker().name == "none"


# --- MMR ------------------------------------------------------------------


async def test_the_top_candidate_is_always_kept():
    """Diversity never costs the best hit: the first pick has nothing to be redundant with."""
    candidates = [retrieved("a", 0.9, text=REVENUE), retrieved("b", 0.85, text=REVENUE_AGAIN)]
    assert names(await MaximalMarginalRelevance(0.5).rerank("q", candidates, limit=1)) == ["a"]


async def test_a_near_duplicate_loses_its_place_to_a_different_chunk():
    """The point of the reranker. `b` restates `a`; `c` says something else.

    Pure relevance would return a, b. MMR returns a, c - the same top hit, then
    the chunk that adds a fact instead of repeating one.
    """
    candidates = [
        retrieved("a", 0.90, text=REVENUE),
        retrieved("b", 0.85, text=REVENUE_AGAIN),
        retrieved("c", 0.40, text=EXPORTS),
    ]
    assert names(await MaximalMarginalRelevance(0.5).rerank("q", candidates, limit=2)) == ["a", "c"]


async def test_lambda_one_is_indistinguishable_from_no_reranking():
    """Pure relevance. Stated as a test because it is how the benchmark isolates
    the diversity term from the rest of the pipeline."""
    candidates = [
        retrieved("a", 0.90, text=REVENUE),
        retrieved("b", 0.85, text=REVENUE_AGAIN),
        retrieved("c", 0.40, text=EXPORTS),
    ]
    assert names(await MaximalMarginalRelevance(1.0).rerank("q", candidates, limit=3)) == names(
        await NoReranker().rerank("q", candidates, limit=3)
    )


async def test_distinct_chunks_are_left_in_relevance_order():
    """Nothing is redundant here, so there is nothing for diversity to fix."""
    candidates = [
        retrieved("a", 0.9, text=REVENUE),
        retrieved("b", 0.8, text=EXPORTS),
        retrieved("c", 0.7, text=MEMORY),
    ]
    assert names(await MaximalMarginalRelevance(0.7).rerank("q", candidates, limit=3)) == [
        "a",
        "b",
        "c",
    ]


async def test_a_reranked_chunk_records_what_it_was_ranked_by():
    """`score` and `rerank_score` agree, and the fused score is still there."""
    candidates = [retrieved("a", 0.9, text=REVENUE), retrieved("b", 0.5, text=EXPORTS)]
    ranked = await MaximalMarginalRelevance(0.7).rerank("q", candidates, limit=2)
    assert all(hit.rerank_score == hit.score for hit in ranked)
    assert [hit.fusion_score for hit in ranked] == [0.9, 0.5]


async def test_the_candidate_set_is_never_added_to():
    """A reranker reorders and cuts. Anything else would bypass the filters."""
    candidates = [retrieved("a", 0.9), retrieved("b", 0.8)]
    ranked = await MaximalMarginalRelevance().rerank("q", candidates, limit=10)
    assert {hit.chunk.id for hit in ranked} == {hit.chunk.id for hit in candidates}


async def test_identical_text_does_not_bury_every_copy():
    """Duplicates are demoted, not dropped: a caller asking for 3 still gets 3."""
    candidates = [retrieved(name, 0.9, text=REVENUE) for name in ("a", "b", "c")]
    assert len(await MaximalMarginalRelevance(0.5).rerank("q", candidates, limit=3)) == 3


async def test_an_empty_chunk_is_not_similar_to_everything():
    """Jaccard over empty token sets is 0, not 1. Otherwise one empty chunk
    would make every other chunk look like a duplicate of it."""
    candidates = [retrieved("a", 0.9, text=""), retrieved("b", 0.8, text=""), retrieved("c", 0.7)]
    assert len(await MaximalMarginalRelevance(0.5).rerank("q", candidates, limit=3)) == 3


async def test_no_candidates_reranks_to_nothing():
    assert await MaximalMarginalRelevance().rerank("q", [], limit=5) == []


def test_lambda_outside_zero_to_one_is_refused():
    """A negative lambda inverts relevance - it would rank the worst hit first."""
    with pytest.raises(ValueError, match="between 0 and 1"):
        MaximalMarginalRelevance(1.5)


def test_the_reranker_names_its_own_lambda():
    """Recorded on the result, so a benchmark row says which setting produced it."""
    assert MaximalMarginalRelevance(0.7).name == "mmr(lambda=0.7)"
