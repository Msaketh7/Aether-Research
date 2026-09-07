"""The OpenAI provider.

Chat Completions rather than the newer Responses API, deliberately: it is the
surface Ollama and most OpenAI-compatible gateways also speak, so one adapter
shape covers the cases an operator is likely to point this at. When the project
needs a Responses-only feature, that is a new capability flag and a second code
path, not a silent rewrite of this one.

Two provider facts that shape the code:

* **Streaming omits usage unless asked.** `stream_options={"include_usage":
  True}` is required, or every streamed call reports no tokens and the cost
  ledger quietly under-counts.
* **There is no token-counting endpoint.** `count_tokens` raises rather than
  estimating with a tokenizer that may not match the model - Phase 16 makes
  budget decisions with this number, and a confident wrong answer is worse than
  an honest refusal.

No models are declared for this provider in the shipped registry, because the
repository cannot verify current model ids or prices; see `registry.yaml`. The
adapter is complete and tested, so enabling it is a configuration edit.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Sequence
from typing import Any, NoReturn

import openai
from openai import AsyncOpenAI

from app.core.enums import LlmProvider
from app.core.logging import get_logger
from app.models.base import (
    ChatMessage,
    Completion,
    CompletionChunk,
    CompletionRequest,
    EmbeddingResult,
    StructuredCompletion,
    StructuredT,
    TokenUsage,
)
from app.models.errors import (
    CapabilityNotSupported,
    ContextWindowExceeded,
    ProviderRateLimited,
    ProviderRefused,
    ProviderRejectedRequest,
    ProviderTimeout,
    ProviderUnavailable,
    StructuredOutputInvalid,
)

logger = get_logger(__name__)

_FINISH_REASONS = {
    "stop": "stop",
    "length": "length",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "content_filter": "refusal",
}


class OpenAIProvider:
    """``LLMProvider`` backed by an OpenAI-compatible Chat Completions API."""

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float,
        base_url: str | None = None,
        http_client: Any | None = None,
    ) -> None:
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            # Retries and failover belong to the gateway; see the note in the
            # Anthropic adapter.
            max_retries=0,
            **({"http_client": http_client} if http_client is not None else {}),
        )

    @property
    def name(self) -> LlmProvider:
        return LlmProvider.OPENAI

    # --- operations ------------------------------------------------------

    async def generate(self, request: CompletionRequest) -> Completion:
        started = time.perf_counter()
        try:
            response = await self._client.chat.completions.create(**self._payload(request))
        except Exception as exc:
            _fail(exc, model=request.model, operation="generate")
        return self._to_completion(response, started)

    async def generate_structured(
        self,
        request: CompletionRequest,
        schema: type[StructuredT],
    ) -> StructuredCompletion[StructuredT]:
        started = time.perf_counter()
        try:
            response = await self._client.chat.completions.parse(
                **self._payload(request),
                response_format=schema,
            )
        except Exception as exc:
            _fail(exc, model=request.model, operation="generate_structured")

        completion = self._to_completion(response, started)
        parsed = response.choices[0].message.parsed if response.choices else None
        if not isinstance(parsed, schema):
            raise StructuredOutputInvalid(
                context={
                    "model": request.model,
                    "schema": schema.__name__,
                    "text": completion.text[:200],
                }
            )
        return StructuredCompletion(value=parsed, completion=completion)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        usage: TokenUsage | None = None
        finish_reason: str | None = None
        try:
            stream = await self._client.chat.completions.create(
                **self._payload(request),
                stream=True,
                # Without this the final chunk carries no usage and the call is
                # invisible to cost accounting.
                stream_options={"include_usage": True},
            )
            async for event in stream:
                if event.usage is not None:
                    usage = TokenUsage(
                        prompt_tokens=int(event.usage.prompt_tokens),
                        completion_tokens=int(event.usage.completion_tokens),
                    )
                if not event.choices:
                    continue
                choice = event.choices[0]
                if choice.finish_reason:
                    finish_reason = _FINISH_REASONS.get(choice.finish_reason, "other")
                if choice.delta and choice.delta.content:
                    yield CompletionChunk(delta=choice.delta.content)
        except Exception as exc:
            _fail(exc, model=request.model, operation="stream")

        yield CompletionChunk(
            delta="",
            usage=usage or TokenUsage(prompt_tokens=0, completion_tokens=0),
            finish_reason=finish_reason or "stop",
        )

    async def embed(self, texts: Sequence[str], *, model: str) -> EmbeddingResult:
        started = time.perf_counter()
        try:
            response = await self._client.embeddings.create(model=model, input=list(texts))
        except Exception as exc:
            _fail(exc, model=model, operation="embed")

        return EmbeddingResult(
            vectors=[item.embedding for item in response.data],
            provider=self.name,
            model=str(response.model),
            usage=TokenUsage(
                prompt_tokens=int(response.usage.prompt_tokens),
                completion_tokens=0,
            ),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def count_tokens(self, request: CompletionRequest) -> int:
        raise CapabilityNotSupported(
            "OpenAI has no token-counting endpoint; counting locally would not be "
            "guaranteed to match the model's own tokenizer.",
            context={"provider": self.name.value, "model": request.model},
        )

    async def check(self) -> bool:
        try:
            await self._client.models.list()
        except Exception as exc:
            logger.warning(
                "openai provider check failed",
                extra={"provider": self.name.value, "error": str(exc)},
            )
            return False
        return True

    async def close(self) -> None:
        await self._client.close()

    # --- internals -------------------------------------------------------

    def _payload(self, request: CompletionRequest) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if request.prompt.system:
            messages.append({"role": "system", "content": request.prompt.system})
        messages.extend(_message(message) for message in request.prompt.messages)

        payload: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_completion_tokens": request.max_output_tokens,
        }
        if request.stop:
            payload["stop"] = list(request.stop)
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.effort:
            payload["reasoning_effort"] = request.effort
        if request.timeout_seconds is not None:
            payload["timeout"] = request.timeout_seconds
        return payload

    def _to_completion(self, response: Any, started: float) -> Completion:
        choice = response.choices[0] if response.choices else None
        raw_finish = getattr(choice, "finish_reason", None)
        finish_reason = _FINISH_REASONS.get(str(raw_finish), "other")

        if finish_reason == "refusal":
            raise ProviderRefused(
                context={"model": str(response.model), "finish_reason": str(raw_finish)}
            )

        refusal = getattr(getattr(choice, "message", None), "refusal", None)
        if refusal:
            raise ProviderRefused(
                context={"model": str(response.model), "explanation": str(refusal)}
            )

        message = getattr(choice, "message", None)
        text = (message.content if message is not None else None) or ""
        usage = response.usage
        return Completion(
            text=text,
            provider=self.name,
            model=str(response.model),
            usage=TokenUsage(
                prompt_tokens=int(usage.prompt_tokens) if usage else 0,
                completion_tokens=int(usage.completion_tokens) if usage else 0,
            ),
            finish_reason=finish_reason,
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=getattr(response, "_request_id", None),
        )


def _message(message: ChatMessage) -> dict[str, str]:
    return {"role": message.role.value, "content": message.content}


def _fail(exc: Exception, *, model: str, operation: str) -> NoReturn:
    from app.core.errors import AppError

    if isinstance(exc, AppError):
        raise exc

    context: dict[str, Any] = {"provider": "openai", "model": model, "operation": operation}

    if isinstance(exc, openai.APITimeoutError):
        raise ProviderTimeout(context={**context, "error": str(exc)}) from exc
    if isinstance(exc, openai.RateLimitError):
        retry_after = exc.response.headers.get("retry-after") if exc.response else None
        raise ProviderRateLimited(
            retry_after_seconds=float(retry_after) if retry_after else None,
            context={**context, "error": str(exc)},
        ) from exc
    if isinstance(exc, openai.APIConnectionError):
        raise ProviderUnavailable(context={**context, "error": str(exc)}) from exc
    if isinstance(exc, openai.APIStatusError):
        context["status"] = exc.status_code
        if exc.status_code >= 500:
            raise ProviderUnavailable(context={**context, "error": str(exc)}) from exc
        if exc.status_code == 400 and _is_context_error(str(exc)):
            raise ContextWindowExceeded(context={**context, "error": str(exc)}) from exc
        raise ProviderRejectedRequest(context={**context, "error": str(exc)}) from exc

    raise ProviderUnavailable(context={**context, "error": str(exc)}) from exc


def _is_context_error(message: str) -> bool:
    lowered = message.lower()
    return "context" in lowered or "maximum context length" in lowered
