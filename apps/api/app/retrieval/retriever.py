"""The ``Retriever`` interface and the Postgres implementation behind it.

ADR 0003 fixed the shape: the interface is ours, ``retrieve()`` /
``retrieve_with_filters()`` / ``retrieve_hybrid()``, and whatever does the work
sits behind it. This implementation is raw SQL against the one system of record
(ADR 0004) - two arms, fused by reciprocal rank, then reranked. A LlamaIndex
retriever, or an external index, is a second implementation of this Protocol and
the benchmark is what decides between them.

The three methods are not three strategies. They are three levels of saying how
much you know about what you want:

* ``retrieve`` - a run and a question. The configured default plan.
* ``retrieve_with_filters`` - the same, narrowed to part of the corpus.
* ``retrieve_hybrid`` - the same, with the search itself specified.

Two invariants hold across all of them, and both are enforced here rather than
trusted to callers:

**Ownership.** Every arm goes through ``_chunk_selection``, which puts
``research_runs.user_id`` in the WHERE clause. A retriever is the easiest place
in the system to leak another user's corpus, because the caller is an agent and
agents do not carry an identity of their own.

**Honest emptiness.** An arm that could not run says so, with a reason. No
embedding model configured, nothing embedded yet, both weights at zero - each
produces a skipped arm on the result, never a quietly shorter list.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from typing import Protocol
from uuid import UUID

from app.db.repositories.documents import SqlAlchemyDocumentRepository
from app.db.session import Database
from app.retrieval.embedding import QueryEmbedder
from app.retrieval.filters import ChunkFilter
from app.retrieval.fusion import FusedChunk, reciprocal_rank_fusion
from app.retrieval.query import QueryText, RetrievalPlan
from app.retrieval.rerank import MaximalMarginalRelevance, NoReranker, Reranker
from app.retrieval.results import (
    ArmOutcome,
    RetrievalResult,
    RetrievalStrategy,
    RetrievedChunk,
    ScoredChunk,
)

_NO_EMBEDDER = "No embedding model is configured, so nothing can be searched by meaning."
_WEIGHT_ZERO = "This arm's fusion weight is zero."


class Retriever(Protocol):
    """What the agent layer may ask of retrieval (ADR 0003)."""

    async def retrieve(
        self,
        query: str,
        *,
        run_id: UUID,
        user_id: UUID,
        limit: int | None = None,
    ) -> RetrievalResult:
        """The default strategy over a whole run's corpus."""
        ...

    async def retrieve_with_filters(
        self,
        query: str,
        *,
        filters: ChunkFilter,
        user_id: UUID,
        limit: int | None = None,
    ) -> RetrievalResult:
        """The default strategy over the part of the corpus ``filters`` admits."""
        ...

    async def retrieve_hybrid(
        self,
        query: str,
        *,
        filters: ChunkFilter,
        user_id: UUID,
        plan: RetrievalPlan | None = None,
    ) -> RetrievalResult:
        """Both arms, fused and reranked exactly as ``plan`` specifies."""
        ...


class PostgresRetriever:
    """Hybrid retrieval over ``document_chunks``.

    ``query_embedder=None`` is a supported deployment, not a degraded one: with
    no embedding model configured there are no vectors to search, and the
    lexical arm over the generated ``tsv`` column works on its own. What is not
    supported is pretending otherwise, so the dense arm comes back skipped with
    a reason attached.
    """

    def __init__(
        self,
        *,
        database: Database,
        query_embedder: QueryEmbedder | None = None,
        reranker: Reranker | None = None,
        default_plan: RetrievalPlan | None = None,
    ) -> None:
        self._database = database
        self._query_embedder = query_embedder
        self._reranker = reranker or MaximalMarginalRelevance()
        self._default_plan = default_plan or RetrievalPlan()

    @property
    def default_plan(self) -> RetrievalPlan:
        return self._default_plan

    async def retrieve(
        self,
        query: str,
        *,
        run_id: UUID,
        user_id: UUID,
        limit: int | None = None,
    ) -> RetrievalResult:
        return await self.retrieve_with_filters(
            query, filters=ChunkFilter(run_id=run_id), user_id=user_id, limit=limit
        )

    async def retrieve_with_filters(
        self,
        query: str,
        *,
        filters: ChunkFilter,
        user_id: UUID,
        limit: int | None = None,
    ) -> RetrievalResult:
        plan = self._default_plan
        if limit is not None:
            # Rebuilt rather than copied: `model_copy` does not re-validate, so
            # a limit past the plan's own ceiling would be accepted here and
            # then silently clamped in SQL - a declared bound that does not hold.
            # Raising the candidate count with it matters too, since fusion can
            # only return what the arms fetched.
            plan = RetrievalPlan.model_validate(
                {
                    **plan.model_dump(),
                    "limit": limit,
                    "candidates": max(plan.candidates, limit),
                }
            )
        return await self.retrieve_hybrid(query, filters=filters, user_id=user_id, plan=plan)

    async def retrieve_hybrid(
        self,
        query: str,
        *,
        filters: ChunkFilter,
        user_id: UUID,
        plan: RetrievalPlan | None = None,
    ) -> RetrievalResult:
        started = time.perf_counter()
        plan = plan or self._default_plan
        text = QueryText(text=query).text

        # The arms are independent - a network call and a database query - so
        # a hybrid retrieval costs the slower of the two, not their sum.
        (dense, dense_arm), (lexical, lexical_arm) = await asyncio.gather(
            self._dense_arm(text, filters, user_id=user_id, plan=plan),
            self._lexical_arm(text, filters, user_id=user_id, plan=plan),
        )

        rankings: dict[RetrievalStrategy, Sequence[ScoredChunk]] = {}
        if dense_arm.ran:
            rankings[RetrievalStrategy.DENSE] = dense
        if lexical_arm.ran:
            rankings[RetrievalStrategy.LEXICAL] = lexical

        weights = {
            RetrievalStrategy.DENSE: plan.dense_weight,
            RetrievalStrategy.LEXICAL: plan.lexical_weight,
        }
        fused = reciprocal_rank_fusion(rankings, weights=weights, k=plan.rrf_k)
        candidates = [_as_retrieved(item) for item in fused]

        reranker: Reranker = self._reranker if plan.rerank else NoReranker()
        ranked = await reranker.rerank(text, candidates, limit=plan.limit)

        return RetrievalResult(
            strategy=plan.strategy,
            chunks=tuple(ranked),
            arms=(dense_arm, lexical_arm),
            candidates=len(candidates),
            reranker=reranker.name if plan.rerank else None,
            latency_ms=_elapsed_ms(started),
        )

    # --- arms -------------------------------------------------------------

    async def _dense_arm(
        self,
        query: str,
        filters: ChunkFilter,
        *,
        user_id: UUID,
        plan: RetrievalPlan,
    ) -> tuple[list[ScoredChunk], ArmOutcome]:
        started = time.perf_counter()
        if not plan.wants_dense:
            reason = _WEIGHT_ZERO if plan.dense_weight <= 0 else "This plan is lexical only."
            return [], _skipped(RetrievalStrategy.DENSE, reason, started)
        if self._query_embedder is None:
            return [], _skipped(RetrievalStrategy.DENSE, _NO_EMBEDDER, started)

        # Embedding happens outside the database session: it is a network call
        # with its own timeout and retries, and holding a pooled connection
        # across it would make a slow provider look like a database outage.
        vector = await self._query_embedder.embed(query, run_id=filters.run_id)
        async with self._database.session() as session:
            hits = await SqlAlchemyDocumentRepository(session).search_dense(
                vector,
                filters,
                user_id=user_id,
                embedding_model=self._query_embedder.model_label,
                limit=plan.candidates,
            )
        return hits, ArmOutcome(
            strategy=RetrievalStrategy.DENSE,
            ran=True,
            returned=len(hits),
            latency_ms=_elapsed_ms(started),
        )

    async def _lexical_arm(
        self,
        query: str,
        filters: ChunkFilter,
        *,
        user_id: UUID,
        plan: RetrievalPlan,
    ) -> tuple[list[ScoredChunk], ArmOutcome]:
        started = time.perf_counter()
        if not plan.wants_lexical:
            reason = _WEIGHT_ZERO if plan.lexical_weight <= 0 else "This plan is dense only."
            return [], _skipped(RetrievalStrategy.LEXICAL, reason, started)

        async with self._database.session() as session:
            hits = await SqlAlchemyDocumentRepository(session).search_lexical(
                query, filters, user_id=user_id, limit=plan.candidates
            )
        return hits, ArmOutcome(
            strategy=RetrievalStrategy.LEXICAL,
            ran=True,
            returned=len(hits),
            latency_ms=_elapsed_ms(started),
        )


def _skipped(strategy: RetrievalStrategy, reason: str, started: float) -> ArmOutcome:
    return ArmOutcome(
        strategy=strategy,
        ran=False,
        returned=0,
        latency_ms=_elapsed_ms(started),
        skipped_reason=reason,
    )


def _as_retrieved(item: FusedChunk) -> RetrievedChunk:
    ranks: Mapping[RetrievalStrategy, int] = item.ranks
    scores: Mapping[RetrievalStrategy, float] = item.scores
    return RetrievedChunk(
        chunk=item.hit.chunk,
        score=item.score,
        fusion_score=item.score,
        dense_rank=ranks.get(RetrievalStrategy.DENSE),
        dense_score=scores.get(RetrievalStrategy.DENSE),
        lexical_rank=ranks.get(RetrievalStrategy.LEXICAL),
        lexical_score=scores.get(RetrievalStrategy.LEXICAL),
    )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
