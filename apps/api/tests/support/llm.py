"""Fake provider endpoints for the model-gateway tests.

Both vendor SDKs are built on ``httpx2``, and so is the Ollama adapter, so one
mock-transport approach covers all three. The SDK still does everything it
normally does - builds the request, signs the headers, serialises the body,
parses the response, decodes the SSE or NDJSON stream, and raises its own typed
exceptions - and only the socket is replaced.

That line is drawn deliberately. Stubbing at the *client* level (patching
``messages.create`` to return a canned object) would leave the parts most likely
to be wrong completely unexercised: the request shape, the streaming decoder, and
the exception classes the failure taxonomy is built on. Every defect this phase
actually found was in one of those.

What is not covered, and is stated rather than implied: the real network, real
authentication, and whether the vendors' current responses still match these
fixtures. A live smoke test against a real key belongs in Phase 19.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

import httpx2 as httpx

Handler = Callable[[httpx.Request], httpx.Response]


def transport(handler: Handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def always(response: httpx.Response) -> Handler:
    def handler(_: httpx.Request) -> httpx.Response:
        return response

    return handler


def sequence(responses: Sequence[httpx.Response]) -> Handler:
    """Answer with each response in turn, repeating the last one.

    Repeating rather than exhausting keeps a retry test from failing with a
    confusing StopIteration when the retry count changes.
    """
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        index = min(calls["n"], len(responses) - 1)
        calls["n"] += 1
        return responses[index]

    return handler


def recording(handler: Handler) -> tuple[Handler, list[dict[str, Any]]]:
    """Wrap a handler, capturing each request body for assertion.

    How a test proves a *negative* - that `temperature` was not sent to a model
    that rejects it - which is otherwise invisible from the response alone.
    """
    seen: list[dict[str, Any]] = []

    def wrapper(request: httpx.Request) -> httpx.Response:
        body = request.content
        seen.append(json.loads(body) if body else {})
        return handler(request)

    return wrapper, seen


# --- Anthropic ------------------------------------------------------------


def anthropic_message(
    text: str = "an answer",
    *,
    model: str = "claude-opus-5",
    input_tokens: int = 11,
    output_tokens: int = 7,
    stop_reason: str = "end_turn",
    parsed: dict[str, Any] | None = None,
) -> httpx.Response:
    content: list[dict[str, Any]] = [
        {"type": "text", "text": json.dumps(parsed) if parsed is not None else text}
    ]
    return httpx.Response(
        200,
        json={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        },
        headers={"request-id": "req_test"},
    )


def anthropic_refusal(model: str = "claude-opus-5") -> httpx.Response:
    """A refusal is HTTP 200 with a stop reason, not an error status."""
    return httpx.Response(
        200,
        json={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": "refusal",
            "stop_details": {"type": "refusal", "category": "cyber", "explanation": "declined"},
            "usage": {"input_tokens": 5, "output_tokens": 0},
        },
    )


def anthropic_stream(
    deltas: Sequence[str],
    *,
    model: str = "claude-opus-5",
    input_tokens: int = 9,
    output_tokens: int = 4,
) -> httpx.Response:
    """A real SSE body, so the SDK's stream decoder runs."""
    events: list[str] = [
        _sse(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "model": model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": input_tokens, "output_tokens": 0},
                },
            },
        ),
        _sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
    ]
    events += [
        _sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": delta},
            },
        )
        for delta in deltas
    ]
    events += [
        _sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            },
        ),
        _sse("message_stop", {"type": "message_stop"}),
    ]
    return httpx.Response(
        200,
        content="".join(events).encode(),
        headers={"content-type": "text/event-stream"},
    )


def anthropic_token_count(tokens: int = 42) -> httpx.Response:
    return httpx.Response(200, json={"input_tokens": tokens})


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# --- OpenAI ---------------------------------------------------------------


def openai_completion(
    text: str = "an answer",
    *,
    model: str = "test-model",
    prompt_tokens: int = 13,
    completion_tokens: int = 5,
    finish_reason: str = "stop",
    refusal: str | None = None,
) -> httpx.Response:
    message: dict[str, Any] = {"role": "assistant", "content": text}
    if refusal is not None:
        message["refusal"] = refusal
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl_test",
            "object": "chat.completion",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        },
    )


def openai_stream(
    deltas: Sequence[str],
    *,
    model: str = "test-model",
    prompt_tokens: int = 8,
    completion_tokens: int = 3,
) -> httpx.Response:
    chunks = [
        {
            "id": "chatcmpl_test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
        }
        for delta in deltas
    ]
    chunks.append(
        {
            "id": "chatcmpl_test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
    )
    # The usage-bearing final chunk, which only arrives when the request asked
    # for it. A test that omitted it would not notice a missing stream_options.
    chunks.append(
        {
            "id": "chatcmpl_test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": model,
            "choices": [],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
    )
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


def openai_embeddings(vectors: Sequence[Sequence[float]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "object": "list",
            "model": "test-embed",
            "data": [
                {"object": "embedding", "index": i, "embedding": list(vector)}
                for i, vector in enumerate(vectors)
            ],
            "usage": {"prompt_tokens": 4, "total_tokens": 4},
        },
    )


# --- Ollama ---------------------------------------------------------------


def ollama_chat(
    text: str = "an answer",
    *,
    model: str = "llama3.1:8b",
    prompt_eval_count: int = 12,
    eval_count: int = 6,
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": model,
            "message": {"role": "assistant", "content": text},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": prompt_eval_count,
            "eval_count": eval_count,
        },
    )


def ollama_stream(
    deltas: Sequence[str],
    *,
    model: str = "llama3.1:8b",
    prompt_eval_count: int = 7,
    eval_count: int = 2,
) -> httpx.Response:
    """Newline-delimited JSON, which is what Ollama actually sends."""
    lines = [
        json.dumps({"model": model, "message": {"content": delta}, "done": False})
        for delta in deltas
    ]
    lines.append(
        json.dumps(
            {
                "model": model,
                "message": {"content": ""},
                "done": True,
                "done_reason": "stop",
                "prompt_eval_count": prompt_eval_count,
                "eval_count": eval_count,
            }
        )
    )
    return httpx.Response(200, content=("\n".join(lines) + "\n").encode())


def ollama_embeddings(vectors: Sequence[Sequence[float]]) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "nomic-embed-text",
            "embeddings": [list(vector) for vector in vectors],
            "prompt_eval_count": 3,
        },
    )


def ollama_tags(names: Sequence[str]) -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": name} for name in names]})
