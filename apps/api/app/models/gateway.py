"""The LLM gateway: one door for every model call (TDD 6.3).

Agents ask for a **role**, not a model:

    completion = await gateway.generate(
        role=AgentName.PLANNER, mode=ResearchMode.DEEP, prompt=prompt
    )

Everything between that call and the provider happens here, once, for every
caller - which is the entire argument for the indirection. Spread across agent
nodes, each of these becomes a thing that is right in seven places and missing in
the eighth:

* **A bound on every call.** Per-request timeout, bounded retries, and a
  concurrency semaphore so fifty parallel researchers become eight active calls
  and forty-two queued rather than fifty simultaneous connections and a
  rate-limit wall.
* **Retry that knows what it is retrying.** Exponential backoff with jitter for
  the failures backoff can fix, immediate failover for the ones it cannot, and
  no retry at all for a rejected credential.
* **Failover down a declared chain.** A provider outage costs a research run
  some latency instead of the run.
* **A record of every attempt** - tokens, cost, latency, status - so a run's
  spend is a measured number and not an estimate.
* **Capability enforcement.** A model that rejects `temperature` never receives
  it; a model with no embeddings endpoint is never asked for a vector.

Deliberately *not* here yet, each with its own phase: response caching (15),
budget enforcement (16), OpenTelemetry spans (17). The seams are left where they
go - the recorder is an interface, and every call already passes through one
place.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from uuid import UUID

from app.core.enums import AgentName, LlmCallStatus, LlmProvider, ResearchMode
from app.core.logging import get_logger
from app.models.base import (
    Completion,
    CompletionChunk,
    CompletionRequest,
    EmbeddingPurpose,
    EmbeddingResult,
    LLMProvider,
    Prompt,
    StructuredCompletion,
    StructuredT,
    TokenUsage,
)
from app.models.errors import CapabilityNotSupported, ModelError, ModelNotConfigured
from app.models.recording import CallRecorder, LlmCallRecord
from app.models.registry import ModelRegistry, ModelSpec
from app.models.routing import ModelRouter, RoutingDecision

logger = get_logger(__name__)

#: How a concrete operation is performed once the gateway has chosen a model.
#: Passing the operation in keeps the retry, failover, bounding and accounting
#: written once rather than duplicated per method.
Invoke = Callable[[LLMProvider, CompletionRequest], Awaitable[Completion]]


class LLMGateway:
    """The single choke point for model access."""

    def __init__(
        self,
        *,
        registry: ModelRegistry,
        router: ModelRouter,
        providers: Mapping[LlmProvider, LLMProvider],
        recorder: CallRecorder,
        max_concurrent_calls: int = 8,
        max_attempts_per_model: int = 3,
        request_timeout_seconds: float = 60.0,
        retry_base_delay_seconds: float = 0.5,
        retry_max_delay_seconds: float = 8.0,
    ) -> None:
        self._registry = registry
        self._router = router
        self._providers = dict(providers)
        self._recorder = recorder
        self._semaphore = asyncio.Semaphore(max_concurrent_calls)
        self._max_attempts = max_attempts_per_model
        self._timeout = request_timeout_seconds
        self._base_delay = retry_base_delay_seconds
        self._max_delay = retry_max_delay_seconds

    # --- public surface --------------------------------------------------

    async def generate(
        self,
        *,
        role: AgentName,
        mode: ResearchMode,
        prompt: Prompt,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        stop: Sequence[str] = (),
        effort: str | None = None,
        run_id: UUID | None = None,
    ) -> Completion:
        """Generate text for a role."""

        async def call(provider: LLMProvider, request: CompletionRequest) -> Completion:
            return await provider.generate(request)

        return await self._attempt(
            role=role,
            mode=mode,
            prompt=prompt,
            operation="generate",
            invoke=call,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            stop=stop,
            effort=effort,
            run_id=run_id,
        )

    async def generate_structured(
        self,
        *,
        role: AgentName,
        mode: ResearchMode,
        prompt: Prompt,
        schema: type[StructuredT],
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        effort: str | None = None,
        run_id: UUID | None = None,
    ) -> StructuredCompletion[StructuredT]:
        """Generate a response validated against ``schema``."""
        holder: list[StructuredCompletion[StructuredT]] = []

        async def call(provider: LLMProvider, request: CompletionRequest) -> Completion:
            structured = await provider.generate_structured(request, schema)
            holder.append(structured)
            return structured.completion

        await self._attempt(
            role=role,
            mode=mode,
            prompt=prompt,
            operation="generate_structured",
            invoke=call,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            stop=(),
            effort=effort,
            run_id=run_id,
            require_structured_output=True,
        )
        return holder[-1]

    async def stream(
        self,
        *,
        role: AgentName,
        mode: ResearchMode,
        prompt: Prompt,
        max_output_tokens: int | None = None,
        temperature: float | None = None,
        run_id: UUID | None = None,
    ) -> AsyncIterator[CompletionChunk]:
        """Stream a response for a role.

        No retry and no failover once the first token has been delivered: the
        caller has already seen output, and silently restarting on a different
        model would splice two different answers together. The stream fails and
        says so instead. Failures *before* the first chunk are the caller's to
        retry, which is a decision only the caller can make - a partially
        rendered answer is not something the gateway can un-render.
        """
        decision = self._router.resolve(role=role, mode=mode)
        spec = decision.primary
        if not spec.supports_streaming:
            raise CapabilityNotSupported(
                "The model routed to this role does not support streaming.",
                context={"role": role.value, "model": spec.key},
            )

        provider = self._provider_for(spec)
        request = self._request(spec, prompt, max_output_tokens, temperature, (), None)

        text_length = 0
        usage = TokenUsage(prompt_tokens=0, completion_tokens=0)
        status = LlmCallStatus.OK
        error_code: str | None = None
        started = asyncio.get_running_loop().time()

        async with self._semaphore:
            try:
                async for chunk in provider.stream(request):
                    if chunk.usage is not None:
                        usage = chunk.usage
                    text_length += len(chunk.delta)
                    yield chunk
            except ModelError as exc:
                status, error_code = LlmCallStatus.ERROR, exc.code
                raise
            finally:
                latency_ms = int((asyncio.get_running_loop().time() - started) * 1000)
                await self._record(
                    spec=spec,
                    decision=decision,
                    operation="stream",
                    status=status,
                    usage=usage,
                    latency_ms=latency_ms,
                    prompt_version=prompt.version,
                    attempt=1,
                    error_code=error_code,
                    run_id=run_id,
                )

    def embedding_model(self) -> ModelSpec:
        """The model every embedding is produced with.

        Exposed so the ingestion pipeline can compare its declared width with
        the vector column once, when it is built, instead of learning about a
        mismatch from a failed write.
        """
        return self._router.embedding_model()

    async def embed(
        self,
        texts: Sequence[str],
        *,
        purpose: EmbeddingPurpose = EmbeddingPurpose.DOCUMENT,
        run_id: UUID | None = None,
    ) -> EmbeddingResult:
        """Embed texts with the one configured embedding model.

        Not routed by role, and never failed over: vectors from different models
        are not comparable, so an index built from a mixture is not searchable,
        and a "successful" fallback would quietly corrupt it. Failures a pause
        can fix are retried on the same model with the same backoff as
        generation, and every attempt is recorded - the failed ones included.

        ``purpose`` selects the model's task prefix (Phase 8). An asymmetric
        model wants a different one on a stored passage and on a question, and
        the registry is where that is declared - so a caller embeds a query by
        saying it is a query, not by knowing which models need which string.

        Phase 7 made this the first real caller and found it had none of that:
        no gateway timeout, no retry, and a failed call left no record.
        """
        spec = self._router.embedding_model()
        provider = self._provider_for(spec)
        prefix = spec.embedding_prefix(purpose)
        prepared = [prefix + text for text in texts] if prefix else list(texts)

        for attempt in range(1, self._max_attempts + 1):
            started = asyncio.get_running_loop().time()
            try:
                async with self._semaphore:
                    async with asyncio.timeout(self._timeout):
                        result = await provider.embed(prepared, model=spec.model_id)
            except (ModelError, TimeoutError) as exc:
                error = _as_model_error(exc, spec)
                await self._record(
                    spec=spec,
                    decision=None,
                    operation="embed",
                    status=LlmCallStatus.ERROR,
                    usage=TokenUsage(prompt_tokens=0, completion_tokens=0),
                    latency_ms=int((asyncio.get_running_loop().time() - started) * 1000),
                    prompt_version="n/a",
                    attempt=attempt,
                    error_code=error.code,
                    run_id=run_id,
                )
                if error.retryable and attempt < self._max_attempts:
                    await asyncio.sleep(self._backoff(attempt, error))
                    continue
                raise error from exc

            await self._record(
                spec=spec,
                decision=None,
                operation="embed",
                status=LlmCallStatus.OK,
                usage=result.usage,
                latency_ms=result.latency_ms,
                prompt_version="n/a",
                attempt=attempt,
                run_id=run_id,
            )
            return result

        raise ModelNotConfigured(  # pragma: no cover - the loop returns or raises
            "The embedding retry loop ended without a result or an error.",
            context={"model": spec.key},
        )

    async def count_tokens(
        self,
        *,
        role: AgentName,
        mode: ResearchMode,
        prompt: Prompt,
    ) -> int:
        """Count a prompt before spending on it.

        Raises ``CapabilityNotSupported`` when the routed provider has no
        counting endpoint. Callers must handle that rather than assume a number
        is always available - two of the three providers cannot do this.
        """
        decision = self._router.resolve(role=role, mode=mode)
        spec = decision.primary
        provider = self._provider_for(spec)
        request = self._request(spec, prompt, None, None, (), None)
        return await provider.count_tokens(request)

    async def check(self) -> dict[str, bool]:
        """Reachability per configured provider, for an operations view.

        Not wired into ``/ready``: the API process makes no model calls, and a
        provider outage must not stop it serving history, evidence and reports
        that are already in the database.
        """
        names = list(self._providers)
        results = await asyncio.gather(
            *(self._providers[name].check() for name in names),
            return_exceptions=True,
        )
        return {name.value: (result is True) for name, result in zip(names, results, strict=True)}

    async def close(self) -> None:
        for provider in self._providers.values():
            await provider.close()

    # --- internals -------------------------------------------------------

    async def _attempt(
        self,
        *,
        role: AgentName,
        mode: ResearchMode,
        prompt: Prompt,
        operation: str,
        invoke: Invoke,
        max_output_tokens: int | None,
        temperature: float | None,
        stop: Sequence[str],
        effort: str | None,
        run_id: UUID | None,
        require_structured_output: bool = False,
    ) -> Completion:
        """Walk the fallback chain, retrying where retrying can help.

        The two loops are different on purpose. The inner one retries the *same*
        model for failures a pause fixes - a 429, a 5xx, a timeout. The outer one
        moves to the *next* model for failures it cannot - a context window too
        small, a provider that is simply down. Collapsing them would either
        hammer a dead provider or give up on a transient blip.
        """
        decision = self._router.resolve(role=role, mode=mode)
        chain = [
            spec
            for spec in decision.chain
            if not require_structured_output or spec.supports_structured_output
        ]
        if not chain:
            raise ModelNotConfigured(
                "No model routed to this role supports structured output.",
                context={"role": role.value, "chain": [s.key for s in decision.chain]},
            )

        previous_model: str | None = None
        last_error: ModelError | None = None

        for spec in chain:
            provider = self._provider_for(spec)
            request = self._request(spec, prompt, max_output_tokens, temperature, stop, effort)

            for attempt in range(1, self._max_attempts + 1):
                started = asyncio.get_running_loop().time()
                try:
                    async with self._semaphore:
                        async with asyncio.timeout(self._timeout):
                            completion = await invoke(provider, request)
                except (ModelError, TimeoutError) as exc:
                    error = _as_model_error(exc, spec)
                    last_error = error
                    await self._record(
                        spec=spec,
                        decision=decision,
                        operation=operation,
                        status=LlmCallStatus.ERROR,
                        usage=TokenUsage(prompt_tokens=0, completion_tokens=0),
                        latency_ms=int((asyncio.get_running_loop().time() - started) * 1000),
                        prompt_version=prompt.version,
                        attempt=attempt,
                        error_code=error.code,
                        fell_back_from=previous_model,
                        run_id=run_id,
                    )

                    if error.retryable and attempt < self._max_attempts:
                        await asyncio.sleep(self._backoff(attempt, error))
                        continue
                    if error.failover:
                        break  # next model in the chain
                    raise error from exc

                await self._record(
                    spec=spec,
                    decision=decision,
                    operation=operation,
                    status=(LlmCallStatus.FALLBACK if previous_model else LlmCallStatus.OK),
                    usage=completion.usage,
                    latency_ms=completion.latency_ms,
                    prompt_version=prompt.version,
                    attempt=attempt,
                    fell_back_from=previous_model,
                    run_id=run_id,
                    request_id=completion.request_id,
                )
                return completion

            previous_model = spec.key

        if last_error is None:  # pragma: no cover - the loop cannot exit otherwise
            raise ModelNotConfigured(
                "The fallback chain produced no result and no error.",
                context={"role": role.value, "chain": [spec.key for spec in chain]},
            )
        logger.error(
            "every model in the chain failed",
            extra={
                "role": role.value,
                "mode": mode.value,
                "chain": [spec.key for spec in chain],
                "last_error": last_error.code,
            },
        )
        raise last_error

    def _request(
        self,
        spec: ModelSpec,
        prompt: Prompt,
        max_output_tokens: int | None,
        temperature: float | None,
        stop: Sequence[str],
        effort: str | None,
    ) -> CompletionRequest:
        """Build a request the selected model will actually accept.

        Two clamps that exist because getting them wrong is a hard failure:
        output tokens are capped at the model's own ceiling, and temperature is
        dropped for models that reject it outright rather than being sent and
        turned into a 400.
        """
        ceiling = spec.max_output_tokens
        resolved_tokens = min(max_output_tokens or ceiling, ceiling)

        resolved_temperature = temperature
        if temperature is not None and not spec.supports_temperature:
            logger.debug(
                "temperature dropped: model does not accept it",
                extra={"model": spec.key},
            )
            resolved_temperature = None

        return CompletionRequest(
            model=spec.model_id,
            prompt=prompt,
            max_output_tokens=resolved_tokens,
            temperature=resolved_temperature,
            stop=tuple(stop),
            effort=effort,
            timeout_seconds=self._timeout,
        )

    def _provider_for(self, spec: ModelSpec) -> LLMProvider:
        provider = self._providers.get(spec.provider)
        if provider is None:
            raise ModelNotConfigured(
                "The registry declares a model whose provider is not configured.",
                context={
                    "model": spec.key,
                    "provider": spec.provider.value,
                    "configured": [name.value for name in self._providers],
                },
            )
        return provider

    def _backoff(self, attempt: int, error: ModelError) -> float:
        """Exponential backoff with jitter, honouring `Retry-After`.

        Jitter matters more than the curve: fifty researchers that all fail at
        once and all retry at exactly 1.0s reproduce the burst that rate-limited
        them in the first place.
        """
        advised: float | None = getattr(error, "retry_after_seconds", None)
        if advised is not None:
            return min(float(advised), self._max_delay)
        exponential = self._base_delay * (2 ** (attempt - 1))
        jittered: float = exponential + random.uniform(0, self._base_delay)  # noqa: S311
        return min(jittered, self._max_delay)

    async def _record(
        self,
        *,
        spec: ModelSpec,
        decision: RoutingDecision | None,
        operation: str,
        status: LlmCallStatus,
        usage: TokenUsage,
        latency_ms: int,
        prompt_version: str,
        attempt: int,
        error_code: str | None = None,
        fell_back_from: str | None = None,
        run_id: UUID | None = None,
        request_id: str | None = None,
    ) -> None:
        await self._recorder.record(
            LlmCallRecord(
                role=decision.role if decision else AgentName.RESEARCHER,
                mode=decision.mode if decision else ResearchMode.DEEP,
                provider=spec.provider,
                model=spec.model_id,
                operation=operation,
                status=status,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cost_usd=spec.cost_usd(usage),
                latency_ms=latency_ms,
                prompt_version=prompt_version,
                attempt=attempt,
                fell_back_from=fell_back_from,
                error_code=error_code,
                run_id=run_id,
                request_id=request_id,
            )
        )


def _as_model_error(exc: BaseException, spec: ModelSpec) -> ModelError:
    """A gateway-level timeout is a provider timeout from the caller's view."""
    if isinstance(exc, ModelError):
        return exc
    from app.models.errors import ProviderTimeout

    return ProviderTimeout(context={"model": spec.key, "error": str(exc) or type(exc).__name__})
