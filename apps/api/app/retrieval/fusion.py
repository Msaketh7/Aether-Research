"""Reciprocal Rank Fusion: combining two rankings that do not share a scale.

The dense arm returns cosine similarities in [0, 1]; the lexical arm returns
``ts_rank_cd`` values that depend on how many query terms matched and how close
together. Adding or averaging those two numbers is meaningless - they are not
the same unit, and neither is calibrated across queries. Normalising each arm's
scores into [0, 1] per query is the usual alternative, and it is worse than it
looks: it makes the top hit of a query that found nothing relevant score
exactly as high as the top hit of a query that found the answer.

RRF sidesteps the problem by throwing the scores away and keeping only the
order::

    score(d) = sum over arms of  weight(arm) / (k + rank(d, arm))

A document ranked first by one arm and unranked by the other still scores well;
a document ranked tenth by both outscores one ranked second by one and missing
from the other only when ``k`` is small. That trade-off is what ``k`` controls,
and 60 is the value from the paper that introduced the method (Cormack, Clarke
and Buettcher, 2009). It is a documented default here, not a measurement - the
retrieval benchmark is what can move it.

Weights exist because the arms are not equally trustworthy in every deployment:
a corpus with no embeddings at all has a dense arm that returns nothing, and a
deployment of mostly non-English documents has a lexical arm that cannot stem
them. Both are configuration, not code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID

from app.retrieval.results import RetrievalStrategy, ScoredChunk

#: The constant from the original RRF paper. Large enough that the difference
#: between rank 1 and rank 2 does not swamp a second arm's opinion entirely.
DEFAULT_RRF_K = 60


@dataclass(frozen=True, slots=True)
class FusedChunk:
    """One chunk's fused position, with each arm's contribution kept."""

    hit: ScoredChunk
    score: float
    #: 1-based rank per arm; an arm that never returned this chunk is absent.
    ranks: Mapping[RetrievalStrategy, int]
    scores: Mapping[RetrievalStrategy, float]


def reciprocal_rank_fusion(
    rankings: Mapping[RetrievalStrategy, Sequence[ScoredChunk]],
    *,
    weights: Mapping[RetrievalStrategy, float] | None = None,
    k: int = DEFAULT_RRF_K,
) -> list[FusedChunk]:
    """Fuse per-arm rankings into one, best first.

    Each sequence must already be in that arm's own best-first order; position
    is the rank. A chunk appearing in several arms is one entry, and the first
    arm to mention it supplies the ``ChunkView`` - they are the same row read by
    the same query, so any of them would do.

    Ties are broken by chunk id, so that fusing the same rankings twice produces
    the same order. Without it a benchmark's numbers would drift with dictionary
    ordering, and an A/B between two strategies would measure the noise.
    """
    if k < 1:
        raise ValueError("RRF k must be at least 1.")

    contributions: dict[UUID, dict[RetrievalStrategy, int]] = {}
    arm_scores: dict[UUID, dict[RetrievalStrategy, float]] = {}
    hits: dict[UUID, ScoredChunk] = {}
    totals: dict[UUID, float] = {}

    for strategy, ranked in rankings.items():
        weight = 1.0 if weights is None else weights.get(strategy, 1.0)
        if weight <= 0:
            # A zero or negative weight disables an arm. Dropping it here rather
            # than adding zeroes keeps `found_by` honest: the arm contributed
            # nothing, so it should not appear to have found anything.
            continue
        for position, hit in enumerate(ranked, start=1):
            chunk_id = hit.chunk.id
            hits.setdefault(chunk_id, hit)
            existing = contributions.setdefault(chunk_id, {})
            # A chunk cannot appear twice in one arm's ranking; if it somehow
            # did, the better rank is the one that counts.
            if position < existing.get(strategy, position + 1):
                existing[strategy] = position
            else:
                existing.setdefault(strategy, position)
            arm_scores.setdefault(chunk_id, {})[strategy] = hit.score
            totals[chunk_id] = totals.get(chunk_id, 0.0) + weight / (k + position)

    fused = [
        FusedChunk(
            hit=hits[chunk_id],
            score=total,
            ranks=dict(contributions[chunk_id]),
            scores=dict(arm_scores[chunk_id]),
        )
        for chunk_id, total in totals.items()
    ]
    fused.sort(key=lambda item: (-item.score, item.hit.chunk.id))
    return fused
