"""Retrieval, measured from outside (Phase 17).

A decorator around the ``Retriever`` protocol rather than a change inside
``PostgresRetriever``, for the reason ADR 0003 made that a protocol in the
first place: what is being measured is the *seam*, and a second implementation
- a different vector store, a hosted reranker - gets the same numbers without
being asked to emit them.

The two things worth watching are how long a retrieval takes and how much it
returns. A retrieval that got slower and a retrieval that quietly started
returning two chunks instead of ten look identical from the outside until one
of them is a graph, and the run that follows either is a poor report with no
explanation attached.

**Nothing is held on the decorator between calls.** Several researchers
retrieve at once inside one graph, on one instance of this class; a count
stashed on ``self`` would be read by whichever call finished next. The count
travels with the call, in the object the context manager yields.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from app.observability.instruments import observe_retrieval
from app.observability.metrics import Metrics
from app.observability.tracing import span
from app.retrieval.filters import ChunkFilter
from app.retrieval.query import RetrievalPlan
from app.retrieval.results import RetrievalResult
from app.retrieval.retriever import Retriever


class MeasuredRetriever:
    """A retriever that times itself and counts what it found."""

    def __init__(self, inner: Retriever, metrics: Metrics) -> None:
        self._inner = inner
        self._metrics = metrics

    async def retrieve(
        self, query: str, *, run_id: UUID, user_id: UUID, limit: int | None = None
    ) -> RetrievalResult:
        async with self._measured("retrieve") as found:
            result = await self._inner.retrieve(query, run_id=run_id, user_id=user_id, limit=limit)
            found.append(len(result.chunks))
        return result

    async def retrieve_with_filters(
        self, query: str, *, filters: ChunkFilter, user_id: UUID, limit: int | None = None
    ) -> RetrievalResult:
        async with self._measured("retrieve_with_filters") as found:
            result = await self._inner.retrieve_with_filters(
                query, filters=filters, user_id=user_id, limit=limit
            )
            found.append(len(result.chunks))
        return result

    async def retrieve_hybrid(
        self,
        query: str,
        *,
        filters: ChunkFilter,
        user_id: UUID,
        plan: RetrievalPlan | None = None,
    ) -> RetrievalResult:
        async with self._measured("retrieve_hybrid") as found:
            result = await self._inner.retrieve_hybrid(
                query, filters=filters, user_id=user_id, plan=plan
            )
            found.append(len(result.chunks))
        return result

    @asynccontextmanager
    async def _measured(self, operation: str) -> AsyncIterator[list[int]]:
        """Time one retrieval and record how much it returned.

        The observation is in ``finally``, so a retrieval that raised is timed
        too: the slow failures are the ones worth seeing, and a histogram that
        only contains successes hides exactly the tail that matters. A call
        that returned nothing appends nothing and is recorded as zero results,
        which is a real measurement rather than a missing one.
        """
        found: list[int] = []
        started = time.perf_counter()
        try:
            with span(f"retrieval.{operation}"):
                yield found
        finally:
            observe_retrieval(
                self._metrics,
                seconds=time.perf_counter() - started,
                results=found[0] if found else 0,
            )
