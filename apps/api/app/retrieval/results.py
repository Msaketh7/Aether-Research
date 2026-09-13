"""What a retrieval call returns, and what it says about how it got there.

A retrieval result is read by a critic that decides whether to research more, by
an evidence extractor that quotes from it, and by a benchmark that scores it.
All three need the same thing from it: not just the chunks, but enough of the
working to tell *nothing matched* from *that half of the search did not run*.
An empty dense arm because no chunk is embedded yet, and an empty dense arm
because the query genuinely has no neighbours, are different facts, and a bare
list of chunks conflates them.

So every arm reports itself - whether it ran, what it returned, why it was
skipped - and every chunk keeps its per-arm rank and score alongside the fused
one. The alternative is a number with no provenance, which is the thing this
system exists not to produce.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.retrieval.filters import ChunkView


class RetrievalStrategy(StrEnum):
    """How a retrieval call searched.

    ``DENSE`` and ``LEXICAL`` are also the names of the two arms of ``HYBRID``,
    so one vocabulary names both a whole strategy and its halves - which is what
    lets the benchmark report all three against each other.
    """

    #: Nearest neighbours by embedding. Finds paraphrase; misses rare tokens.
    DENSE = "dense"
    #: Postgres full-text over the generated ``tsv`` column. The reverse.
    LEXICAL = "lexical"
    #: Both, fused by reciprocal rank.
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    """One chunk as a single arm ranked it. ``score``: larger is better."""

    chunk: ChunkView
    score: float


@dataclass(frozen=True, slots=True)
class ArmOutcome:
    """What one arm of a hybrid search contributed, including nothing.

    ``skipped_reason`` is prose meant to be read by a person looking at a run
    that returned less than they expected: "no embedding model is configured"
    is an operator's answer, and ``0`` is not.
    """

    strategy: RetrievalStrategy
    ran: bool
    returned: int
    latency_ms: int
    skipped_reason: str | None = None

    @property
    def skipped(self) -> bool:
        return not self.ran


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    """A chunk in the final ranking, with the working that put it there.

    ``score`` is what the ranking is by: the reranker's score where one ran,
    the fusion score otherwise. The per-arm ranks and scores are kept because
    "the lexical arm found this at rank 1 and the dense arm never saw it" is
    the shape of a retrieval bug, and it is invisible once the scores are fused.
    """

    chunk: ChunkView
    score: float
    fusion_score: float
    dense_rank: int | None = None
    dense_score: float | None = None
    lexical_rank: int | None = None
    lexical_score: float | None = None
    rerank_score: float | None = None

    @property
    def id(self) -> UUID:
        return self.chunk.id

    @property
    def found_by(self) -> tuple[RetrievalStrategy, ...]:
        """Which arms surfaced this chunk at all."""
        return tuple(
            strategy
            for strategy, rank in (
                (RetrievalStrategy.DENSE, self.dense_rank),
                (RetrievalStrategy.LEXICAL, self.lexical_rank),
            )
            if rank is not None
        )


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """The answer to one retrieval call."""

    strategy: RetrievalStrategy
    chunks: tuple[RetrievedChunk, ...]
    arms: tuple[ArmOutcome, ...]
    #: Distinct chunks the arms produced between them, before the final cut.
    candidates: int
    #: The reranker that ordered them, by name, or ``None`` if none ran.
    reranker: str | None
    latency_ms: int

    def __len__(self) -> int:
        return len(self.chunks)

    def __bool__(self) -> bool:
        return bool(self.chunks)

    @property
    def chunk_ids(self) -> tuple[UUID, ...]:
        return tuple(hit.chunk.id for hit in self.chunks)

    @property
    def source_ids(self) -> tuple[UUID, ...]:
        """Distinct sources represented, in rank order. What a citation needs."""
        seen: dict[UUID, None] = {}
        for hit in self.chunks:
            seen.setdefault(hit.chunk.source_id, None)
        return tuple(seen)

    @property
    def skipped_arms(self) -> tuple[ArmOutcome, ...]:
        """Arms that did not run. Empty results with a reason, not zero results."""
        return tuple(arm for arm in self.arms if arm.skipped)

    def arm(self, strategy: RetrievalStrategy) -> ArmOutcome | None:
        return next((arm for arm in self.arms if arm.strategy is strategy), None)
