"""Reordering the fused candidates before they become a report's evidence.

Fusion answers "which chunks match?". It does not answer "which eight chunks
should a synthesiser read?", and the two differ for a reason specific to this
system: **the top of a fused list is full of near-duplicates.** Chunks overlap
by 64 tokens by construction, a filing restates its own risk factors, and ten
outlets repost one wire story. A top-8 that is one fact eight times looks
excellent by every ranking metric and starves the report of everything else.

So the reranker here is a *diversity* reranker - Maximal Marginal Relevance -
and it is named for what it does rather than for what the design document
eventually wants:

    MMR(d) = lambda * relevance(d) - (1 - lambda) * max similarity(d, chosen)

**This is not the cross-encoder the TDD (section 8.2) describes.** A
cross-encoder re-scores true relevance by reading query and chunk together, and
it is the right eventual answer; it is also a model this repository cannot run
or price today, and choosing one before the benchmark of this phase exists would
be exactly the anticipation ADR 0004 warns against. The interface is the point:
``Reranker`` is async and takes the query, so a cross-encoder or an LLM judge
drops in behind it and the benchmark says whether it earned its latency.

Similarity is measured over the chunk *text*, as a Jaccard overlap of stemmed-ish
token sets, not over the embeddings. Three reasons: it is available for every
candidate, including a deployment with no embedding model at all; redundancy
between two chunks is a property of what they say, which their words carry
directly; and it costs one pass over text already in memory rather than a
column read of several kilobytes a row.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol

from app.retrieval.results import RetrievedChunk

#: Words too common to say anything about whether two chunks are redundant.
#: Deliberately short: this is a similarity signal between two texts of a few
#: hundred tokens, not a search index, and an aggressive stop list would make
#: short chunks look identical because all that survived was punctuation.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "was",
        "were",
        "will",
        "with",
    }
)

_WORD = re.compile(r"[a-z0-9]+")

#: Below this, two chunks are not meaningfully redundant and the penalty is
#: noise. Above it, MMR starts pushing the second copy down.
_SIMILARITY_FLOOR = 0.10


class Reranker(Protocol):
    """Reorders fused candidates and cuts them to ``limit``.

    Async because the implementations worth having later are network calls. The
    contract: return at most ``limit`` of the candidates given, never anything
    else, with ``score`` set to whatever this reranker ranked by.
    """

    @property
    def name(self) -> str:
        """Recorded on the result, so a benchmark row says what produced it."""

    async def rerank(
        self, query: str, candidates: Sequence[RetrievedChunk], *, limit: int
    ) -> list[RetrievedChunk]:
        """The best ``limit`` candidates, best first."""
        ...


class NoReranker:
    """Keeps the fused order and truncates. The honest name for doing nothing.

    Used when reranking is switched off, and as the control arm in the
    benchmark: "hybrid" and "hybrid + rerank" are only comparable if one of them
    really is unreranked.
    """

    @property
    def name(self) -> str:
        return "none"

    async def rerank(
        self, query: str, candidates: Sequence[RetrievedChunk], *, limit: int
    ) -> list[RetrievedChunk]:
        return list(candidates[:limit])


class MaximalMarginalRelevance:
    """Greedy MMR over the fused candidates.

    ``lambda_`` is the trade: 1.0 is pure relevance (and so identical to no
    reranking), 0.0 is pure novelty, which happily promotes an irrelevant chunk
    for the sin of being different. The default leans towards relevance because
    the fused ranking is the only relevance signal available - there is no
    second opinion to fall back on if diversity throws the good hits away.
    """

    def __init__(self, lambda_: float = 0.7) -> None:
        if not 0.0 <= lambda_ <= 1.0:
            raise ValueError("MMR lambda must be between 0 and 1.")
        self._lambda = lambda_

    @property
    def name(self) -> str:
        return f"mmr(lambda={self._lambda:g})"

    async def rerank(
        self, query: str, candidates: Sequence[RetrievedChunk], *, limit: int
    ) -> list[RetrievedChunk]:
        if limit < 1 or not candidates:
            return []

        # Relevance normalised across this candidate set, because fusion scores
        # are ~1/60 and similarity is ~1; subtracting one from the other raw
        # would make lambda meaningless.
        scores = [candidate.score for candidate in candidates]
        high, low = max(scores), min(scores)
        span = high - low
        relevance = [
            1.0 if span == 0 else (candidate.score - low) / span for candidate in candidates
        ]
        tokens = [_tokenize(candidate.chunk.text.expose()) for candidate in candidates]

        remaining = list(range(len(candidates)))
        chosen: list[int] = []
        # Similarity against the chosen set only, kept incrementally: each round
        # compares every remaining candidate with the one just picked, so the
        # whole pass is O(n * limit) rather than O(n^2). A candidate's penalty
        # stops changing the moment it is chosen, which is what makes the score
        # reported below the same one it was chosen by.
        penalty = [0.0] * len(candidates)

        def marginal(index: int) -> float:
            return self._lambda * relevance[index] - (1.0 - self._lambda) * penalty[index]

        while remaining and len(chosen) < limit:
            # Ties go to the better fused rank: `-index` makes the earlier
            # candidate the larger key.
            best = max(remaining, key=lambda index: (marginal(index), -index))
            chosen.append(best)
            remaining.remove(best)
            for index in remaining:
                similarity = _jaccard(tokens[index], tokens[best])
                # Below the floor, two chunks are not meaningfully redundant and
                # the penalty would be noise.
                if similarity >= _SIMILARITY_FLOOR and similarity > penalty[index]:
                    penalty[index] = similarity

        return [
            replace(candidates[index], score=marginal(index), rerank_score=marginal(index))
            for index in chosen
        ]


def _tokenize(text: str) -> frozenset[str]:
    return frozenset(word for word in _WORD.findall(text.lower()) if word not in _STOPWORDS)


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """Overlap of two token sets, 0 when either is empty.

    An empty chunk is not similar to everything; it is a chunk nothing can be
    said about, and treating it as a duplicate would bury every one of them.
    """
    if not left or not right:
        return 0.0
    union = len(left | right)
    return len(left & right) / union if union else 0.0
