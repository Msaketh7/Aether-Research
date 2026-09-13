"""Retrieval metrics: recall@k, precision@k, MRR, nDCG (evaluation doc 3.1).

Pure functions over a ranking and a set of relevant ids. Nothing here touches a
database or a model, which is the point - the same four numbers have to be
computable from a benchmark run, from a stored evaluation row, and from a live
retrieval call, and a metric that can only be produced one way cannot be
compared across the three.

Two conventions, both chosen so that a number never overstates what was
measured:

**Recall is against the labelled set, not the corpus.** ``recall_at_k`` divides
by how many relevant chunks the labels name, so a query whose labels name three
chunks and whose top 10 returns two scores 0.67 - not 0.2 for "2 of 10".

**A query with no labelled relevant chunk returns ``None``, not 0.0.** It is
unmeasurable, not a failure, and averaging it in as zero would let an
incompletely labelled dataset make every strategy look bad equally. ``mean``
here drops the ``None``s and reports how many it dropped.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Mean:
    """An average, with the count it was taken over and the cases it could not score."""

    value: float | None
    measured: int
    unmeasurable: int

    def __str__(self) -> str:
        if self.value is None:
            return "not measured"
        return f"{self.value:.3f} (n={self.measured})"


def recall_at_k(ranked: Sequence[UUID], relevant: Iterable[UUID], k: int) -> float | None:
    """Fraction of the labelled relevant chunks that appear in the top ``k``."""
    wanted = set(relevant)
    if not wanted:
        return None
    found = sum(1 for chunk_id in _top(ranked, k) if chunk_id in wanted)
    return found / len(wanted)


def precision_at_k(ranked: Sequence[UUID], relevant: Iterable[UUID], k: int) -> float | None:
    """Fraction of the top ``k`` that is relevant.

    Divided by ``k``, not by how many chunks came back: a strategy that returned
    three results of which all three were relevant has not achieved precision
    1.0 at k=10, it has failed to fill the context it was asked for.
    """
    wanted = set(relevant)
    if not wanted or k < 1:
        return None
    found = sum(1 for chunk_id in _top(ranked, k) if chunk_id in wanted)
    return found / k


def reciprocal_rank(ranked: Sequence[UUID], relevant: Iterable[UUID]) -> float | None:
    """1 / the position of the first relevant chunk; 0.0 when none is ranked at all."""
    wanted = set(relevant)
    if not wanted:
        return None
    for position, chunk_id in enumerate(ranked, start=1):
        if chunk_id in wanted:
            return 1.0 / position
    return 0.0


def ndcg_at_k(
    ranked: Sequence[UUID],
    relevant: Iterable[UUID] | dict[UUID, float],
    k: int,
) -> float | None:
    """Normalised discounted cumulative gain over the top ``k``.

    Accepts graded relevance as a mapping, or a plain set for binary labels.
    Normalised against the best ranking those same labels allow, so 1.0 means
    "could not have been ordered better", not "found everything" - a query whose
    relevant chunks all came back, in the wrong order, scores below 1.
    """
    gains = dict.fromkeys(relevant, 1.0) if not isinstance(relevant, dict) else dict(relevant)
    if not gains or k < 1:
        return None

    actual = sum(
        gains.get(chunk_id, 0.0) / math.log2(position + 1)
        for position, chunk_id in enumerate(_top(ranked, k), start=1)
    )
    ideal = sum(
        gain / math.log2(position + 1)
        for position, gain in enumerate(sorted(gains.values(), reverse=True)[:k], start=1)
    )
    return actual / ideal if ideal else None


def mean(values: Iterable[float | None]) -> Mean:
    """Average the measurable values and say how many were not."""
    collected = list(values)
    measured = [value for value in collected if value is not None]
    return Mean(
        value=sum(measured) / len(measured) if measured else None,
        measured=len(measured),
        unmeasurable=len(collected) - len(measured),
    )


def _top(ranked: Sequence[UUID], k: int) -> Sequence[UUID]:
    return ranked[: max(0, k)]
