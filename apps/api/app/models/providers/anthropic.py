"""The Anthropic provider.

Three things here are model-behaviour facts rather than style choices, and each
one is a 400 or a silent quality loss if ignored:

* **`temperature` is rejected** by current Anthropic models. The registry
  declares that per model and this adapter honours it; it never sends the
  parameter hoping it is tolerated.
* **`max_tokens` is required**, which is why `CompletionRequest` has no default
  for it. A silent 1024 here truncates a synthesis in production.
* **`stop_reason == "refusal"` is a 200 response**, not an exception. A caller
  that reads `content` without checking gets an empty string and no idea why, so
  it is translated into `ProviderRefused` - a first-class outcome a research run
  records rather than mistakes for a source with nothing in it.

There is **no embeddings API**, so `embed` raises rather than inventing a vector.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any, NoReturn

from app.core.enums import LlmProvider
from app.core.logging import get_logger
from app.models.base import (
    ChatMessage,
    Completion,
    CompletionChunk,
    CompletionRequest,
    EmbeddingResult,
    MessageRole,
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

if TYPE_CHECKING:
    # Type-checking only. The SDK builds several thousand Pydantic models on
    # import - about ten seconds of CPU - and a deployment routed at OpenAI or
    # Ollama, or a test process that never constructs this provider, should not
    # pay for it. The runtime imports are inside the two functions that need
    # them; see ``__init__`` below.
    from anthropic.types import MessageParam

logger = get_logger(__name__)

#: Anthropic's stop reasons mapped onto the vocabulary the gateway records.
_FINISH_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_use",
    "refusal": "refusal",
    "pause_turn": "other",
}


class AnthropicProvider:
    """``LLMProvider`` backed by the Anthropic Messages API."""

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float,
        base_url: str | None = None,
        http_client: Any | None = None,
    ) -> None:
        # Imported here rather than at module scope - see the TYPE_CHECKING
        # block above. A provider is only constructed when a credential exists
        # for it, so this is the point at which the cost is actually owed.
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            # The gateway owns retries, because it also owns the failover chain
            # and the accounting. Two independent retry loops multiply into a
            # request storm nobody budgeted for.
            max_retries=0,
            **({"http_client": http_client} if http_client is not None else {}),
        )

    @property
    def name(self) -> LlmProvider:
        return LlmProvider.ANTHROPIC

    # --- operations ------------------------------------------------------

    async def generate(self, request: CompletionRequest) -> Completion:
        started = time.perf_counter()
        try:
            message = await self._client.messages.create(**self._payload(request))
        except Exception as exc:
            _fail(exc, model=request.model, operation="generate")

        return self._to_completion(message, started)

    async def generate_structured(
        self,
        request: CompletionRequest,
        schema: type[StructuredT],
    ) -> StructuredCompletion[StructuredT]:
        started = time.perf_counter()
        try:
            # `output_format` takes the model class; the SDK derives the JSON
            # schema and merges it into output_config itself. Hand-building
            # output_config here would fight it.
            message = await self._client.messages.parse(
                **self._payload(request),
                output_format=schema,
            )
        except Exception as exc:
            # One handler, not two: the `anthropic.APIError` branch that used to
            # sit above this did exactly the same thing, and `_fail` already
            # sorts SDK errors by type.
            _fail(exc, model=request.model, operation="generate_structured")

        completion = self._to_completion(message, started)
        parsed = getattr(message, "parsed_output", None)
        if not isinstance(parsed, schema):
            # Reached when the model returns text that does not fit the schema.
            # Kept distinct from a provider fault: the fix is the prompt or the
            # schema, and the evaluation suite counts these separately.
            raise StructuredOutputInvalid(
                context={
                    "model": request.model,
                    "schema": schema.__name__,
                    "text": completion.text[:200],
                }
            )
        return StructuredCompletion(value=parsed, completion=completion)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        started = time.perf_counter()
        try:
            async with self._client.messages.stream(**self._payload(request)) as stream:
                async for text in stream.text_stream:
                    yield CompletionChunk(delta=text)
                final = await stream.get_final_message()
        except Exception as exc:
            _fail(exc, model=request.model, operation="stream")

        # The terminal chunk carries usage. Without it a streamed call would be
        # invisible to cost accounting, which is the one thing the gateway must
        # never allow.
        completion = self._to_completion(final, started)
        yield CompletionChunk(
            delta="",
            usage=completion.usage,
            finish_reason=completion.finish_reason,
        )

    async def embed(self, texts: Sequence[str], *, model: str) -> EmbeddingResult:
        raise CapabilityNotSupported(
            "Anthropic does not provide an embeddings API; configure another provider.",
            context={"provider": self.name.value, "model": model, "count": len(texts)},
        )

    async def count_tokens(self, request: CompletionRequest) -> int:
        try:
            system = request.prompt.system
            counted = (
                await self._client.messages.count_tokens(
                    model=request.model,
                    messages=_messages(request.prompt.messages),
                    system=system,
                )
                if system
                else await self._client.messages.count_tokens(
                    model=request.model,
                    messages=_messages(request.prompt.messages),
                )
            )
        except Exception as exc:
            _fail(exc, model=request.model, operation="count_tokens")
        return int(counted.input_tokens)

    async def check(self) -> bool:
        """A one-token call is the only honest reachability test.

        It proves the endpoint answers *and* the credential is accepted, which
        a bare connection check does not.
        """
        try:
            await self._client.messages.count_tokens(
                model="claude-haiku-4-5",
                messages=[{"role": "user", "content": "ping"}],
            )
        except Exception as exc:
            logger.warning(
                "anthropic provider check failed",
                extra={"provider": self.name.value, "error": str(exc)},
            )
            return False
        return True

    async def close(self) -> None:
        await self._client.close()

    # --- internals -------------------------------------------------------

    def _payload(self, request: CompletionRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_output_tokens,
            "messages": _messages(request.prompt.messages),
        }
        if request.prompt.system:
            payload["system"] = request.prompt.system
        if request.stop:
            payload["stop_sequences"] = list(request.stop)
        if request.effort:
            payload["output_config"] = {"effort": request.effort}
        if request.temperature is not None:
            # The gateway strips this for models that declare no temperature
            # support, so reaching here means the model accepts it.
            payload["temperature"] = request.temperature
        if request.timeout_seconds is not None:
            payload["timeout"] = request.timeout_seconds
        return payload

    def _to_completion(self, message: Any, started: float) -> Completion:
        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise ProviderRefused(
                context={
                    "model": message.model,
                    "category": getattr(details, "category", None),
                    "explanation": getattr(details, "explanation", None),
                }
            )

        text = "".join(block.text for block in message.content if block.type == "text")
        usage = TokenUsage(
            prompt_tokens=int(message.usage.input_tokens),
            completion_tokens=int(message.usage.output_tokens),
        )
        return Completion(
            text=text,
            provider=self.name,
            model=str(message.model),
            usage=usage,
            finish_reason=_FINISH_REASONS.get(str(message.stop_reason), "other"),
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=getattr(message, "_request_id", None),
        )


def _messages(messages: Sequence[ChatMessage]) -> list[MessageParam]:
    """Map to Anthropic's message shape.

    A system message that reached the message list is dropped rather than sent:
    Anthropic carries the system instruction in its own field, and a stray
    `role: "system"` entry is either a 400 or, on the models that accept it,
    an operator-authority channel that a prompt must not be able to reach into
    by accident.
    """
    # A dict literal rather than `MessageParam(...)`: the TypedDict constructor
    # only builds a dict, and spelling it this way keeps the SDK out of this
    # module's runtime imports. The annotation still checks the shape.
    return [
        {
            "role": "assistant" if message.role is MessageRole.ASSISTANT else "user",
            "content": message.content,
        }
        for message in messages
        if message.role is not MessageRole.SYSTEM
    ]


def _fail(exc: Exception, *, model: str, operation: str) -> NoReturn:
    """Translate an SDK exception into the model error taxonomy.

    The classification is the product here. A 429 and a 401 are both
    `APIStatusError`; one should be retried and the other must never be.
    """
    # Both imports are local for the same reason the client's is. Reaching here
    # means a call was made, so the SDK is already in ``sys.modules`` and this
    # costs a dictionary lookup.
    import anthropic

    from app.core.errors import AppError

    if isinstance(exc, AppError):
        raise exc

    context: dict[str, Any] = {"provider": "anthropic", "model": model, "operation": operation}

    if isinstance(exc, anthropic.APITimeoutError):
        raise ProviderTimeout(context={**context, "error": str(exc)}) from exc
    if isinstance(exc, anthropic.RateLimitError):
        retry_after = exc.response.headers.get("retry-after") if exc.response else None
        raise ProviderRateLimited(
            retry_after_seconds=float(retry_after) if retry_after else None,
            context={**context, "error": str(exc)},
        ) from exc
    if isinstance(exc, anthropic.APIConnectionError):
        raise ProviderUnavailable(context={**context, "error": str(exc)}) from exc
    if isinstance(exc, anthropic.APIStatusError):
        context["status"] = exc.status_code
        if exc.status_code >= 500:
            raise ProviderUnavailable(context={**context, "error": str(exc)}) from exc
        # A 400 whose message names the context window is a size problem, and a
        # different model can fix it. Every other 4xx is our bug.
        if exc.status_code == 400 and _is_context_error(str(exc)):
            raise ContextWindowExceeded(context={**context, "error": str(exc)}) from exc
        raise ProviderRejectedRequest(context={**context, "error": str(exc)}) from exc

    raise ProviderUnavailable(context={**context, "error": str(exc)}) from exc


def _is_context_error(message: str) -> bool:
    lowered = message.lower()
    return "context" in lowered and ("long" in lowered or "exceed" in lowered)
