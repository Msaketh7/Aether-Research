"""The Ollama provider - local models, no API key.

This is the provider that makes ADR 0007's promise true: the whole agent graph
runs on a laptop with nothing but `ollama serve`, so contributing to this project
does not require a paid account, and a developer can exercise the full research
loop before anyone pays for a token.

Talked to over its native HTTP API with `httpx2` rather than through the
OpenAI-compatible shim. Two reasons: the native API reports `prompt_eval_count`
and `eval_count`, so local calls are accounted for exactly like paid ones instead
of being a hole in the ledger; and `/api/tags` lets `check()` verify the model was
actually pulled, turning a wrong `model_id` in the registry into a startup
failure that names the missing tag rather than a confusing 404 mid-run.

`httpx2` and not `httpx` because both provider SDKs are built on it - one HTTP
client in the runtime rather than two.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any, NoReturn

import httpx2 as httpx

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
    ProviderRejectedRequest,
    ProviderTimeout,
    ProviderUnavailable,
    StructuredOutputInvalid,
)

logger = get_logger(__name__)

_FINISH_REASONS = {"stop": "stop", "length": "length", "load": "other"}


class OllamaProvider:
    """``LLMProvider`` backed by a local Ollama server."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
        )

    @property
    def name(self) -> LlmProvider:
        return LlmProvider.OLLAMA

    # --- operations ------------------------------------------------------

    async def generate(self, request: CompletionRequest) -> Completion:
        started = time.perf_counter()
        body = await self._post("/api/chat", self._payload(request), model=request.model)
        return self._to_completion(body, started)

    async def generate_structured(
        self,
        request: CompletionRequest,
        schema: type[StructuredT],
    ) -> StructuredCompletion[StructuredT]:
        started = time.perf_counter()
        payload = self._payload(request)
        # Ollama constrains decoding to a JSON schema when one is supplied.
        payload["format"] = schema.model_json_schema()

        body = await self._post("/api/chat", payload, model=request.model)
        completion = self._to_completion(body, started)
        try:
            value = schema.model_validate_json(completion.text)
        except ValueError as exc:
            raise StructuredOutputInvalid(
                context={
                    "model": request.model,
                    "schema": schema.__name__,
                    "text": completion.text[:200],
                    "error": str(exc),
                }
            ) from exc
        return StructuredCompletion(value=value, completion=completion)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        started = time.perf_counter()
        payload = self._payload(request)
        payload["stream"] = True

        final: Mapping[str, Any] | None = None
        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code >= 400:
                    await response.aread()
                    _fail_status(response, model=request.model, operation="stream")
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    # Ollama streams newline-delimited JSON, one object per token
                    # batch, with the last one carrying `done` and the counts.
                    event = json.loads(line)
                    if event.get("done"):
                        final = event
                        break
                    if delta := event.get("message", {}).get("content", ""):
                        yield CompletionChunk(delta=delta)
        except httpx.TimeoutException as exc:
            _fail_transport(exc, model=request.model, operation="stream")
        except httpx.HTTPError as exc:
            _fail_transport(exc, model=request.model, operation="stream")

        if final is None:
            raise ProviderUnavailable(
                "The local model stopped streaming before reporting completion.",
                context={"model": request.model, "operation": "stream"},
            )
        completion = self._to_completion(final, started)
        yield CompletionChunk(
            delta="",
            usage=completion.usage,
            finish_reason=completion.finish_reason,
        )

    async def embed(self, texts: Sequence[str], *, model: str) -> EmbeddingResult:
        started = time.perf_counter()
        body = await self._post("/api/embed", {"model": model, "input": list(texts)}, model=model)
        vectors = body.get("embeddings") or []
        return EmbeddingResult(
            vectors=vectors,
            provider=self.name,
            model=str(body.get("model", model)),
            usage=TokenUsage(
                prompt_tokens=int(body.get("prompt_eval_count", 0)),
                completion_tokens=0,
            ),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def count_tokens(self, request: CompletionRequest) -> int:
        raise CapabilityNotSupported(
            "Ollama reports token counts only after generating, so a prompt "
            "cannot be counted before it is spent.",
            context={"provider": self.name.value, "model": request.model},
        )

    async def check(self) -> bool:
        """Is the server up? Reported alongside the tags it has pulled."""
        try:
            response = await self._client.get("/api/tags")
            response.raise_for_status()
        except Exception as exc:
            logger.warning(
                "ollama provider check failed",
                extra={"provider": self.name.value, "error": str(exc)},
            )
            return False
        return True

    async def has_model(self, model_id: str) -> bool:
        """Whether a tag has actually been pulled.

        The registry declares model ids an operator may not have locally. Asking
        here turns "404 during a research run" into a startup message naming the
        missing tag and the `ollama pull` that fixes it.
        """
        try:
            response = await self._client.get("/api/tags")
            response.raise_for_status()
            tags = {str(entry.get("name", "")) for entry in response.json().get("models", [])}
        except Exception:
            return False
        # Ollama normalises a bare name to `name:latest`.
        return model_id in tags or f"{model_id}:latest" in tags

    async def close(self) -> None:
        await self._client.aclose()

    # --- internals -------------------------------------------------------

    def _payload(self, request: CompletionRequest) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if request.prompt.system:
            messages.append({"role": "system", "content": request.prompt.system})
        messages.extend(_message(message) for message in request.prompt.messages)

        options: dict[str, Any] = {"num_predict": request.max_output_tokens}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.stop:
            options["stop"] = list(request.stop)

        return {
            "model": request.model,
            "messages": messages,
            "stream": False,
            "options": options,
        }

    async def _post(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        model: str,
    ) -> Mapping[str, Any]:
        try:
            response = await self._client.post(path, json=dict(payload))
        except httpx.TimeoutException as exc:
            _fail_transport(exc, model=model, operation=path)
        except httpx.HTTPError as exc:
            _fail_transport(exc, model=model, operation=path)

        if response.status_code >= 400:
            _fail_status(response, model=model, operation=path)

        body: Mapping[str, Any] = response.json()
        return body

    def _to_completion(self, body: Mapping[str, Any], started: float) -> Completion:
        text = str(body.get("message", {}).get("content", ""))
        return Completion(
            text=text,
            provider=self.name,
            model=str(body.get("model", "")),
            usage=TokenUsage(
                prompt_tokens=int(body.get("prompt_eval_count", 0)),
                completion_tokens=int(body.get("eval_count", 0)),
            ),
            finish_reason=_FINISH_REASONS.get(str(body.get("done_reason", "stop")), "other"),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


def _message(message: ChatMessage) -> dict[str, str]:
    return {"role": message.role.value, "content": message.content}


def _fail_transport(exc: Exception, *, model: str, operation: str) -> NoReturn:
    context = {"provider": "ollama", "model": model, "operation": operation, "error": str(exc)}
    if isinstance(exc, httpx.TimeoutException):
        raise ProviderTimeout(context=context) from exc
    raise ProviderUnavailable(context=context) from exc


def _fail_status(response: httpx.Response, *, model: str, operation: str) -> NoReturn:
    detail = response.text[:500]
    context = {
        "provider": "ollama",
        "model": model,
        "operation": operation,
        "status": response.status_code,
        "error": detail,
    }
    if response.status_code >= 500:
        raise ProviderUnavailable(context=context)
    if "context" in detail.lower() and "length" in detail.lower():
        raise ContextWindowExceeded(context=context)
    if response.status_code == 404:
        # Ollama's 404 for a model means "not pulled", which a different model
        # in the fallback chain may well satisfy.
        raise ProviderRejectedRequest(
            "That model is not available on the local Ollama server. "
            "Pull it with `ollama pull <model>`.",
            context=context,
        )
    raise ProviderRejectedRequest(context=context)
