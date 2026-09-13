"""Embeddings: the gateway's embed path and the checks a vector passes before storage.

Driven through the real gateway, the real registry and the real Ollama adapter,
with only the socket replaced - the approach every model test here takes.
"""

from __future__ import annotations

import json

import httpx2 as httpx
import pytest

from app.core.config import Settings
from app.core.enums import LlmCallStatus, LlmProvider
from app.models import (
    CollectingCallRecorder,
    ContextWindowExceeded,
    OllamaProvider,
    ProviderRejectedRequest,
    ProviderTimeout,
    build_gateway,
)
from app.retrieval.embedding import ChunkEmbedder, QueryEmbedder
from app.retrieval.errors import EmbeddingDimensionMismatch, EmbeddingResponseInvalid
from tests.support import llm

DIMENSIONS = 768


def gateway_for(handler, recorder: CollectingCallRecorder | None = None):
    settings = Settings(
        app_env="test",
        llm_max_attempts=3,
        llm_retry_base_delay_seconds=0.001,
        llm_retry_max_delay_seconds=0.002,
    )
    provider = OllamaProvider(
        base_url="http://ollama.test", timeout_seconds=5, transport=llm.transport(handler)
    )
    return build_gateway(
        settings,
        providers={LlmProvider.OLLAMA: provider},
        recorder=recorder or CollectingCallRecorder(),
    )


def vectors_for(body: dict, *, dimensions: int = DIMENSIONS) -> httpx.Response:
    return llm.ollama_embeddings(
        [[0.001 * (index + 1)] * dimensions for index, _ in enumerate(body["input"])]
    )


def embeddings(dimensions: int = DIMENSIONS, seen: list[dict] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if seen is not None:
            seen.append(body)
        return vectors_for(body, dimensions=dimensions)

    return handler


# --- ChunkEmbedder ----------------------------------------------------------


async def test_a_batch_is_embedded_one_checked_vector_per_text():
    seen: list[dict] = []
    embedder = ChunkEmbedder(gateway_for(embeddings(seen=seen)), batch_size=8)

    vectors = await embedder.embed(["first chunk", "second chunk"])

    assert len(vectors) == 2
    assert all(len(vector) == DIMENSIONS for vector in vectors)
    assert embedder.model_label == "ollama/nomic-embed-text"
    # Silent truncation is off, so an over-long chunk fails instead of being
    # embedded as a prefix of itself.
    assert seen[0]["truncate"] is False


def test_a_model_whose_width_is_not_the_columns_is_refused_at_construction():
    """At startup, not on the first write - pgvector would refuse every vector."""
    with pytest.raises(EmbeddingDimensionMismatch):
        ChunkEmbedder(gateway_for(embeddings()), batch_size=8, dimensions=1536)


async def test_a_short_response_is_refused_rather_than_misaligned():
    """One vector for two texts would put every vector on the wrong chunk."""

    def one_vector(request: httpx.Request) -> httpx.Response:
        return llm.ollama_embeddings([[0.1] * DIMENSIONS])

    embedder = ChunkEmbedder(gateway_for(one_vector), batch_size=8)
    with pytest.raises(EmbeddingResponseInvalid):
        await embedder.embed(["a", "b"])


async def test_a_vector_of_the_wrong_width_is_refused():
    embedder = ChunkEmbedder(gateway_for(embeddings(dimensions=3)), batch_size=8)
    with pytest.raises(EmbeddingResponseInvalid):
        await embedder.embed(["a"])


async def test_a_non_finite_value_is_refused():
    def with_nan(request: httpx.Request) -> httpx.Response:
        # Written by hand: a JSON encoder refuses NaN, but a server can still
        # send the token, and Python's parser accepts it.
        values = ",".join(["NaN"] + ["0.1"] * (DIMENSIONS - 1))
        body = (
            '{"model": "nomic-embed-text", "embeddings": [[' + values + "]], "
            '"prompt_eval_count": 1}'
        )
        return httpx.Response(200, content=body.encode())

    embedder = ChunkEmbedder(gateway_for(with_nan), batch_size=8)
    with pytest.raises(EmbeddingResponseInvalid):
        await embedder.embed(["a"])


async def test_more_texts_than_a_batch_is_a_caller_error():
    embedder = ChunkEmbedder(gateway_for(embeddings()), batch_size=2)
    with pytest.raises(ValueError):
        await embedder.embed(["a", "b", "c"])


# --- the gateway's embed path ------------------------------------------------


async def test_a_transient_failure_is_retried_and_every_attempt_recorded():
    """Before Phase 7, embed had no retry and a failed call left no record."""
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="overloaded")
        return vectors_for(json.loads(request.content))

    recorder = CollectingCallRecorder()
    result = await gateway_for(flaky, recorder).embed(["a", "b"])

    assert len(result.vectors) == 2
    assert [(call.status, call.attempt) for call in recorder.calls] == [
        (LlmCallStatus.ERROR, 1),
        (LlmCallStatus.OK, 2),
    ]
    assert recorder.calls[0].error_code == "provider_unavailable"
    assert all(call.operation == "embed" for call in recorder.calls)


async def test_a_timeout_is_retried_to_the_limit_then_raised():
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("the model is thinking", request=request)

    recorder = CollectingCallRecorder()
    with pytest.raises(ProviderTimeout):
        await gateway_for(slow, recorder).embed(["a"])
    assert [call.attempt for call in recorder.calls] == [1, 2, 3]


async def test_a_rejected_request_is_not_retried():
    recorder = CollectingCallRecorder()
    with pytest.raises(ProviderRejectedRequest):
        await gateway_for(llm.always(httpx.Response(400, text="bad request")), recorder).embed(
            ["a"]
        )
    assert len(recorder.calls) == 1


async def test_an_over_long_input_fails_loudly_and_never_fails_over():
    """Vectors from two models are not comparable, so embeddings have no fallback."""
    too_long = httpx.Response(400, text="the input length exceeds the context length")
    recorder = CollectingCallRecorder()
    with pytest.raises(ContextWindowExceeded):
        await gateway_for(llm.always(too_long), recorder).embed(["a"])
    assert len(recorder.calls) == 1


# --- task prefixes (Phase 8) ------------------------------------------------


async def test_a_chunk_is_embedded_with_the_models_document_prefix():
    """nomic-embed-text is asymmetric: it is trained with a task prefix on every
    input. Omitting it fails nothing - it just puts stored passages slightly
    away from the questions asked of them, and retrieval quietly gets worse."""
    seen: list[dict] = []
    embedder = ChunkEmbedder(gateway_for(embeddings(seen=seen)), batch_size=8)

    await embedder.embed(["revenue rose in the quarter"])

    assert seen[0]["input"] == ["search_document: revenue rose in the quarter"]


async def test_a_query_is_embedded_with_the_other_prefix():
    seen: list[dict] = []
    embedder = ChunkEmbedder(gateway_for(embeddings(seen=seen)), batch_size=8)

    vector = await QueryEmbedder(embedder).embed("how did revenue change?")

    assert seen[0]["input"] == ["search_query: how did revenue change?"]
    assert len(vector) == DIMENSIONS


async def test_a_query_embedder_reports_the_model_its_chunks_were_embedded_with():
    """The label a dense search filters on. If the two disagreed, a search would
    compare vectors from two models, which share a column but not a space."""
    embedder = ChunkEmbedder(gateway_for(embeddings()), batch_size=8)
    assert QueryEmbedder(embedder).model_label == embedder.model_label


SYMMETRIC_REGISTRY = """
models:
  - key: ollama:embed
    provider: ollama
    model_id: nomic-embed-text
    tier: small
    context_window: 8192
    max_output_tokens: 1
    supports_chat: false
    supports_embeddings: true
    embedding_dimensions: 768
"""


async def test_a_model_that_declares_no_prefix_is_sent_the_text_unchanged(tmp_path):
    """A prefix a model was not trained with is noise added to every vector, so
    an undeclared prefix is empty rather than a guess. Driven from a registry
    file, because whether a model is asymmetric is data, not code."""
    path = tmp_path / "registry.yaml"
    path.write_text(SYMMETRIC_REGISTRY, encoding="utf-8")
    seen: list[dict] = []
    provider = OllamaProvider(
        base_url="http://ollama.test",
        timeout_seconds=5,
        transport=llm.transport(embeddings(seen=seen)),
    )
    gateway = build_gateway(
        Settings(app_env="test", model_registry_path=path),
        providers={LlmProvider.OLLAMA: provider},
        recorder=CollectingCallRecorder(),
    )

    await ChunkEmbedder(gateway, batch_size=8).embed(["revenue rose"])

    assert seen[0]["input"] == ["revenue rose"]
