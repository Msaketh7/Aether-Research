"""Chunk embeddings - through the gateway, and checked before they are stored.

The gateway is the only door to a model (ADR 0007), so every embedding call is
bounded, retried and recorded there. What this module adds is the checks a
vector must pass before it is written, because pgvector accepts plenty of
vectors that are useless and a bad one stays invisible until retrieval quietly
stops finding things:

* one vector per text, in order - a short response would shift every vector
  onto the wrong chunk;
* exactly the column's width - checked against the registry when the embedder
  is built, so a mismatch fails at startup, and again on every response,
  because what gets stored is what the provider sent, not what the registry
  promised;
* finite numbers only - one NaN makes a vector's distance to everything
  undefined.

Embeddings take a chunk's raw characters, not the delimited ``for_prompt()``
form (ADR 0011). An embedding model has no instruction channel to inject into,
and wrapping each chunk in the standing data notice would make every vector
partly a vector of the notice.

The one thing that *is* added is the model's own task prefix, and the gateway
adds it from the registry (Phase 8). ``purpose`` is how a caller says which one:
a stored passage and a question asked of it want different prefixes on an
asymmetric model, and getting that wrong costs recall without failing anything.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from uuid import UUID

from app.db.models.source import EMBEDDING_DIMENSIONS
from app.models import EmbeddingPurpose, LLMGateway
from app.retrieval.errors import EmbeddingDimensionMismatch, EmbeddingResponseInvalid


class ChunkEmbedder:
    """Embeds one batch of chunk texts and validates the result."""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        batch_size: int,
        dimensions: int = EMBEDDING_DIMENSIONS,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1.")
        spec = gateway.embedding_model()
        if spec.embedding_dimensions != dimensions:
            raise EmbeddingDimensionMismatch(
                f"The embedding model {spec.key} is declared with "
                f"{spec.embedding_dimensions} dimensions, but the index stores {dimensions}. "
                "Declare a model of that width, or migrate the column and re-embed.",
                context={
                    "model": spec.key,
                    "declared_dimensions": spec.embedding_dimensions,
                    "column_dimensions": dimensions,
                },
            )
        self._gateway = gateway
        self._batch_size = batch_size
        self._dimensions = dimensions
        # Provider and model id rather than the registry key: an operator can
        # repoint a key at a different model, and vectors from two models must
        # never be labelled as one.
        self._model_label = f"{spec.provider.value}/{spec.model_id}"

    @property
    def model_label(self) -> str:
        """What ``document_chunks.embedding_model`` records."""
        return self._model_label

    @property
    def batch_size(self) -> int:
        return self._batch_size

    async def embed(
        self,
        texts: Sequence[str],
        *,
        purpose: EmbeddingPurpose = EmbeddingPurpose.DOCUMENT,
        run_id: UUID | None = None,
    ) -> list[list[float]]:
        """One vector per text, in order, each checked. At most ``batch_size`` texts."""
        if not texts:
            return []
        if len(texts) > self._batch_size:
            raise ValueError(
                f"At most {self._batch_size} texts per call; batch them before embedding."
            )

        result = await self._gateway.embed(texts, purpose=purpose, run_id=run_id)
        vectors = list(result.vectors)
        if len(vectors) != len(texts):
            raise EmbeddingResponseInvalid(
                context={"expected": len(texts), "received": len(vectors), "model": result.model}
            )

        checked: list[list[float]] = []
        for position, vector in enumerate(vectors):
            if len(vector) != self._dimensions:
                raise EmbeddingResponseInvalid(
                    context={
                        "position": position,
                        "dimensions": len(vector),
                        "expected": self._dimensions,
                    }
                )
            try:
                values = [float(value) for value in vector]
            except (TypeError, ValueError) as exc:
                raise EmbeddingResponseInvalid(
                    context={"position": position, "reason": "not numeric"}
                ) from exc
            if not all(math.isfinite(value) for value in values):
                raise EmbeddingResponseInvalid(
                    context={"position": position, "reason": "non-finite value"}
                )
            checked.append(values)
        return checked


class QueryEmbedder:
    """One vector for a query, from the embedder that produced the chunks.

    Built from a ``ChunkEmbedder`` rather than from the gateway, and that is the
    whole point of the class: a query vector is only meaningful in the space the
    stored vectors occupy, so the two must come from the same model with the
    same width checks. Composing them makes that structural instead of a
    convention two call sites are expected to keep.

    The one thing that differs is the task prefix, and the gateway applies it
    from the registry - see ``EmbeddingPurpose``.
    """

    def __init__(self, embedder: ChunkEmbedder) -> None:
        self._embedder = embedder

    @property
    def model_label(self) -> str:
        """The ``document_chunks.embedding_model`` value a search may compare against."""
        return self._embedder.model_label

    async def embed(self, query: str, *, run_id: UUID | None = None) -> list[float]:
        vectors = await self._embedder.embed([query], purpose=EmbeddingPurpose.QUERY, run_id=run_id)
        return vectors[0]
