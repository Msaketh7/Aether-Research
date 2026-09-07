"""The three provider adapters, against their real SDKs.

Each test drives the vendor SDK all the way to the socket - request building,
serialisation, response parsing, stream decoding, exception classes - and
replaces only the network. See ``tests/support/llm.py`` for where that line is
drawn and why.

What is being protected here is mostly *translation*: the same logical failure
arrives as three different exception types across three SDKs, and the gateway's
retry and failover behaviour is built entirely on getting that mapping right.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import anthropic
import httpx2 as httpx
import pytest
from pydantic import BaseModel

from app.core.enums import LlmProvider
from app.models import (
    AnthropicProvider,
    CapabilityNotSupported,
    ChatMessage,
    CompletionRequest,
    MessageRole,
    OllamaProvider,
    OpenAIProvider,
    Prompt,
    ProviderRateLimited,
    ProviderRefused,
    ProviderRejectedRequest,
    ProviderUnavailable,
)
from tests.support import llm


class Answer(BaseModel):
    """A schema small enough to assert on, real enough to exercise validation."""

    verdict: str
    confidence: int


def a_request(model: str = "claude-opus-5", **overrides: object) -> CompletionRequest:
    defaults: dict[str, object] = {
        "model": model,
        "prompt": Prompt(
            messages=[ChatMessage(role=MessageRole.USER, content="Compare two companies.")],
            system="You are precise.",
            version="test-v1",
        ),
        "max_output_tokens": 256,
    }
    defaults.update(overrides)
    return CompletionRequest(**defaults)  # type: ignore[arg-type]


def anthropic_provider(handler: llm.Handler) -> AnthropicProvider:
    return AnthropicProvider(
        api_key="test-key",
        timeout_seconds=5.0,
        base_url="https://api.test.invalid",
        http_client=anthropic.DefaultAsyncHttpxClient(transport=llm.transport(handler)),
    )


def openai_provider(handler: llm.Handler) -> OpenAIProvider:
    import openai

    return OpenAIProvider(
        api_key="test-key",
        timeout_seconds=5.0,
        base_url="https://api.test.invalid/v1",
        http_client=openai.DefaultAsyncHttpxClient(transport=llm.transport(handler)),
    )


def ollama_provider(handler: llm.Handler) -> OllamaProvider:
    return OllamaProvider(
        base_url="http://localhost:11434",
        timeout_seconds=5.0,
        transport=llm.transport(handler),
    )


async def drain(stream: AsyncIterator[object]) -> list[object]:
    return [chunk async for chunk in stream]


# --- Anthropic ------------------------------------------------------------


async def test_anthropic_returns_text_and_usage():
    provider = anthropic_provider(llm.always(llm.anthropic_message("Acme leads on price.")))

    completion = await provider.generate(a_request())

    assert completion.text == "Acme leads on price."
    assert completion.provider is LlmProvider.ANTHROPIC
    assert completion.usage.prompt_tokens == 11
    assert completion.usage.completion_tokens == 7
    assert completion.finish_reason == "stop"
    await provider.close()


async def test_anthropic_never_sends_temperature_when_the_caller_omits_it():
    """The models this project routes to reject `temperature` with a 400, so
    the parameter must not appear unless it was explicitly asked for."""
    handler, seen = llm.recording(llm.always(llm.anthropic_message()))
    provider = anthropic_provider(handler)

    await provider.generate(a_request())

    assert "temperature" not in seen[0]
    await provider.close()


async def test_anthropic_carries_the_system_prompt_in_its_own_field():
    """Anthropic models the system instruction separately; flattening it into
    the message list loses information the provider needs."""
    handler, seen = llm.recording(llm.always(llm.anthropic_message()))
    provider = anthropic_provider(handler)

    await provider.generate(a_request())

    assert seen[0]["system"] == "You are precise."
    assert all(message["role"] != "system" for message in seen[0]["messages"])
    await provider.close()


async def test_anthropic_refusal_is_an_outcome_not_an_empty_answer():
    """A refusal is HTTP 200 with an empty content list. Read without checking,
    it looks like a source that had nothing in it."""
    provider = anthropic_provider(llm.always(llm.anthropic_refusal()))

    with pytest.raises(ProviderRefused) as raised:
        await provider.generate(a_request())

    assert raised.value.context["category"] == "cyber"
    await provider.close()


async def test_anthropic_streams_deltas_then_a_usage_bearing_chunk():
    """Without the terminal usage chunk, every streamed call would be invisible
    to cost accounting."""
    provider = anthropic_provider(llm.always(llm.anthropic_stream(["Acme ", "leads."])))

    chunks = [chunk async for chunk in provider.stream(a_request())]

    assert "".join(c.delta for c in chunks) == "Acme leads."
    assert chunks[-1].is_final
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.completion_tokens == 4
    await provider.close()


async def test_anthropic_validates_structured_output():
    provider = anthropic_provider(
        llm.always(llm.anthropic_message(parsed={"verdict": "supported", "confidence": 4}))
    )

    result = await provider.generate_structured(a_request(), Answer)

    assert result.value.verdict == "supported"
    assert result.value.confidence == 4
    await provider.close()


async def test_anthropic_counts_tokens_without_generating():
    provider = anthropic_provider(llm.always(llm.anthropic_token_count(1234)))

    assert await provider.count_tokens(a_request()) == 1234
    await provider.close()


async def test_anthropic_has_no_embeddings_and_says_so():
    """The alternative - returning an empty vector - poisons a retrieval index
    in a way that surfaces weeks later as bad search results."""
    provider = anthropic_provider(llm.always(llm.anthropic_message()))

    with pytest.raises(CapabilityNotSupported):
        await provider.embed(["text"], model="claude-opus-5")
    await provider.close()


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (429, ProviderRateLimited),
        (500, ProviderUnavailable),
        (503, ProviderUnavailable),
        (401, ProviderRejectedRequest),
        (400, ProviderRejectedRequest),
    ],
)
async def test_anthropic_status_codes_map_to_retry_semantics(status, expected):
    """A 429 and a 401 are the same SDK exception class. One should be retried
    and the other must never be, so the split has to happen here."""
    provider = anthropic_provider(
        llm.always(httpx.Response(status, json={"error": {"message": "nope"}}))
    )

    with pytest.raises(expected):
        await provider.generate(a_request())
    await provider.close()


async def test_anthropic_rate_limit_carries_the_advised_delay():
    """The provider knows better than our backoff curve does."""
    provider = anthropic_provider(
        llm.always(
            httpx.Response(
                429, json={"error": {"message": "slow down"}}, headers={"retry-after": "7"}
            )
        )
    )

    with pytest.raises(ProviderRateLimited) as raised:
        await provider.generate(a_request())

    assert raised.value.retry_after_seconds == 7.0
    await provider.close()


# --- OpenAI ---------------------------------------------------------------


async def test_openai_returns_text_and_usage():
    provider = openai_provider(llm.always(llm.openai_completion("Beta leads on latency.")))

    completion = await provider.generate(a_request("test-model"))

    assert completion.text == "Beta leads on latency."
    assert completion.provider is LlmProvider.OPENAI
    assert completion.usage.prompt_tokens == 13
    await provider.close()


async def test_openai_puts_the_system_prompt_first_in_the_message_list():
    """The opposite convention from Anthropic, which is exactly the kind of
    difference the shared interface exists to absorb."""
    handler, seen = llm.recording(llm.always(llm.openai_completion()))
    provider = openai_provider(handler)

    await provider.generate(a_request("test-model"))

    assert seen[0]["messages"][0] == {"role": "system", "content": "You are precise."}
    await provider.close()


async def test_openai_streaming_asks_for_usage():
    """Without stream_options the final chunk carries no usage and the cost
    ledger silently under-counts every streamed call."""
    handler, seen = llm.recording(llm.always(llm.openai_stream(["Beta ", "leads."])))
    provider = openai_provider(handler)

    chunks = [chunk async for chunk in provider.stream(a_request("test-model"))]

    assert seen[0]["stream_options"] == {"include_usage": True}
    assert "".join(c.delta for c in chunks) == "Beta leads."
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.prompt_tokens == 8
    await provider.close()


async def test_openai_refusal_is_reported_as_a_refusal():
    provider = openai_provider(llm.always(llm.openai_completion(refusal="I can't help with that.")))

    with pytest.raises(ProviderRefused):
        await provider.generate(a_request("test-model"))
    await provider.close()


async def test_openai_embeds():
    provider = openai_provider(llm.always(llm.openai_embeddings([[0.1, 0.2], [0.3, 0.4]])))

    result = await provider.embed(["a", "b"], model="test-embed")

    assert result.dimensions == 2
    assert len(result.vectors) == 2
    await provider.close()


async def test_openai_refuses_to_guess_a_token_count():
    """A confident wrong number is worse than an honest refusal: Phase 16 makes
    budget decisions with this."""
    provider = openai_provider(llm.always(llm.openai_completion()))

    with pytest.raises(CapabilityNotSupported):
        await provider.count_tokens(a_request("test-model"))
    await provider.close()


@pytest.mark.parametrize(
    ("status", "expected"),
    [(429, ProviderRateLimited), (500, ProviderUnavailable), (401, ProviderRejectedRequest)],
)
async def test_openai_status_codes_map_to_retry_semantics(status, expected):
    provider = openai_provider(
        llm.always(httpx.Response(status, json={"error": {"message": "nope"}}))
    )

    with pytest.raises(expected):
        await provider.generate(a_request("test-model"))
    await provider.close()


# --- Ollama ---------------------------------------------------------------


async def test_ollama_returns_text_and_the_counts_it_reports():
    """Local calls are accounted for exactly like paid ones. A provider that is
    free is not a provider that is exempt from the ledger."""
    provider = ollama_provider(llm.always(llm.ollama_chat("Local answer.")))

    completion = await provider.generate(a_request("llama3.1:8b"))

    assert completion.text == "Local answer."
    assert completion.provider is LlmProvider.OLLAMA
    assert completion.usage.prompt_tokens == 12
    assert completion.usage.completion_tokens == 6
    await provider.close()


async def test_ollama_streams_ndjson():
    """Ollama sends newline-delimited JSON, not SSE."""
    provider = ollama_provider(llm.always(llm.ollama_stream(["Local ", "answer."])))

    chunks = [chunk async for chunk in provider.stream(a_request("llama3.1:8b"))]

    assert "".join(c.delta for c in chunks) == "Local answer."
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.completion_tokens == 2
    await provider.close()


async def test_ollama_constrains_decoding_with_the_json_schema():
    handler, seen = llm.recording(
        llm.always(llm.ollama_chat('{"verdict": "refuted", "confidence": 2}'))
    )
    provider = ollama_provider(handler)

    result = await provider.generate_structured(a_request("llama3.1:8b"), Answer)

    assert seen[0]["format"]["properties"]["verdict"]["type"] == "string"
    assert result.value.verdict == "refuted"
    await provider.close()


async def test_ollama_embeds():
    provider = ollama_provider(llm.always(llm.ollama_embeddings([[0.5, 0.6, 0.7]])))

    result = await provider.embed(["a"], model="nomic-embed-text")

    assert result.dimensions == 3
    await provider.close()


async def test_ollama_reports_a_model_that_was_never_pulled():
    """Turns "404 halfway through a research run" into a startup message that
    names the missing tag."""
    provider = ollama_provider(llm.always(llm.ollama_tags(["llama3.1:8b"])))

    assert await provider.has_model("llama3.1:8b") is True
    assert await provider.has_model("nomic-embed-text") is False
    await provider.close()


async def test_ollama_a_missing_model_is_an_actionable_error():
    provider = ollama_provider(
        llm.always(httpx.Response(404, json={"error": "model 'x' not found"}))
    )

    with pytest.raises(ProviderRejectedRequest) as raised:
        await provider.generate(a_request("x"))

    assert "ollama pull" in raised.value.message
    await provider.close()


async def test_ollama_being_down_is_retryable():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = ollama_provider(refuse)

    with pytest.raises(ProviderUnavailable):
        await provider.generate(a_request("llama3.1:8b"))
    await provider.close()


# --- the interface itself -------------------------------------------------


def test_every_adapter_satisfies_the_protocol():
    """Structural conformance checked at runtime: a method renamed in one
    adapter and not the others would otherwise surface only when configuration
    happened to select it."""
    from app.models import LLMProvider

    handler = llm.always(llm.anthropic_message())
    assert isinstance(anthropic_provider(handler), LLMProvider)
    assert isinstance(openai_provider(handler), LLMProvider)
    assert isinstance(ollama_provider(handler), LLMProvider)
