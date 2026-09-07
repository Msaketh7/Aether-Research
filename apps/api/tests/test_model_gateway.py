"""The gateway: bounds, retry, failover, and the call ledger.

This is the file that matters most in Phase 5. The provider adapters can be
re-read against a vendor's docs; the gateway's behaviour under failure is the
part that decides whether a research run survives a rate limit, whether a
provider outage costs latency or costs the run, and whether the number the
product reports as "what this cost" is a measurement or a guess.

The providers here are scripted fakes rather than mock-transport SDKs. That is
deliberate and it is the opposite choice from ``test_model_providers.py``: what
is under test is the *gateway's* control flow, and driving it with real SDKs
would mean every assertion about retry counts also depended on a vendor's
serialisation. The SDK paths are covered next door, against real SDK code.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import AsyncIterator, Sequence

import pytest
from pydantic import BaseModel

from app.core.enums import AgentName, LlmCallStatus, LlmProvider, ResearchMode
from app.models import (
    ChatMessage,
    CollectingCallRecorder,
    Completion,
    CompletionChunk,
    CompletionRequest,
    EmbeddingResult,
    LLMGateway,
    MessageRole,
    ModelNotConfigured,
    ModelRegistry,
    ModelRouter,
    ModelSpec,
    ModelTier,
    Prompt,
    ProviderRateLimited,
    ProviderRejectedRequest,
    ProviderUnavailable,
    TokenUsage,
)
from app.models.errors import CapabilityNotSupported, ContextWindowExceeded
from app.models.registry import Pricing

PROMPT = Prompt(
    messages=[ChatMessage(role=MessageRole.USER, content="Compare two companies.")],
    system="Be precise.",
    version="planner-v3",
)


class Answer(BaseModel):
    verdict: str


def spec(
    key: str,
    tier: ModelTier = ModelTier.STRONG,
    *,
    provider: LlmProvider = LlmProvider.ANTHROPIC,
    priced: bool = True,
    temperature: bool = True,
    max_output_tokens: int = 4096,
    structured: bool = True,
    streaming: bool = True,
    embeddings: bool = False,
    chat: bool = True,
) -> ModelSpec:
    return ModelSpec(
        key=key,
        provider=provider,
        model_id=key,
        tier=tier,
        context_window=100_000,
        max_output_tokens=max_output_tokens,
        pricing=(Pricing(1.0, 2.0, dt.date(2026, 1, 1), "test") if priced else None),
        supports_chat=chat,
        supports_streaming=streaming,
        supports_structured_output=structured,
        supports_temperature=temperature,
        supports_embeddings=embeddings,
    )


class ScriptedProvider:
    """A provider that replays a script and records what it was asked.

    Each entry is either an exception to raise or the text to return, so a test
    states a failure sequence directly instead of arranging one.
    """

    def __init__(
        self,
        name: LlmProvider = LlmProvider.ANTHROPIC,
        script: Sequence[object] = ("ok",),
    ) -> None:
        self._name = name
        self._script = list(script)
        self.requests: list[CompletionRequest] = []
        self.calls = 0

    @property
    def name(self) -> LlmProvider:
        return self._name

    def _next(self, request: CompletionRequest) -> str:
        self.requests.append(request)
        step = self._script[min(self.calls, len(self._script) - 1)]
        self.calls += 1
        if isinstance(step, Exception):
            raise step
        return str(step)

    async def generate(self, request: CompletionRequest) -> Completion:
        text = self._next(request)
        return Completion(
            text=text,
            provider=self._name,
            model=request.model,
            usage=TokenUsage(prompt_tokens=100, completion_tokens=50),
            finish_reason="stop",
            latency_ms=3,
        )

    async def generate_structured(self, request: CompletionRequest, schema: type[BaseModel]):
        from app.models import StructuredCompletion

        completion = await self.generate(request)
        return StructuredCompletion(value=schema(verdict=completion.text), completion=completion)

    async def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        text = self._next(request)
        for character in text:
            yield CompletionChunk(delta=character)
        yield CompletionChunk(
            delta="",
            usage=TokenUsage(prompt_tokens=100, completion_tokens=50),
            finish_reason="stop",
        )

    async def embed(self, texts: Sequence[str], *, model: str) -> EmbeddingResult:
        return EmbeddingResult(
            vectors=[[0.1, 0.2] for _ in texts],
            provider=self._name,
            model=model,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=0),
            latency_ms=1,
        )

    async def count_tokens(self, request: CompletionRequest) -> int:
        return 99

    async def check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


def build(
    *specs: ModelSpec,
    providers: dict[LlmProvider, ScriptedProvider] | None = None,
    recorder: CollectingCallRecorder | None = None,
    **overrides: object,
) -> tuple[LLMGateway, CollectingCallRecorder]:
    registry = ModelRegistry(specs={s.key: s for s in specs})
    resolved = providers or {LlmProvider.ANTHROPIC: ScriptedProvider()}
    sink = recorder or CollectingCallRecorder()
    kwargs: dict[str, object] = {
        # Retry delays are compressed so a backoff test finishes in
        # milliseconds instead of waiting on a production curve.
        "retry_base_delay_seconds": 0.001,
        "retry_max_delay_seconds": 0.002,
    }
    kwargs.update(overrides)
    gateway = LLMGateway(
        registry=registry,
        router=ModelRouter(registry),
        providers=resolved,  # type: ignore[arg-type]
        recorder=sink,
        **kwargs,  # type: ignore[arg-type]
    )
    return gateway, sink


# --- the happy path -------------------------------------------------------


async def test_a_role_resolves_to_a_model_without_the_caller_naming_one():
    """The entire point of the indirection: an agent asks for a role."""
    gateway, _ = build(spec("strong"))

    completion = await gateway.generate(
        role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT
    )

    assert completion.text == "ok"
    assert completion.model == "strong"


async def test_every_call_is_recorded_with_tokens_cost_and_latency():
    """The unconditional project rule. Enforced here so it cannot be missed at
    the next call site someone adds."""
    gateway, recorder = build(spec("strong"))

    await gateway.generate(role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT)

    (call,) = recorder.calls
    assert call.role is AgentName.CRITIC
    assert call.mode is ResearchMode.DEEP
    assert call.status is LlmCallStatus.OK
    assert call.prompt_tokens == 100
    assert call.completion_tokens == 50
    assert call.cost_usd == pytest.approx(100 / 1e6 * 1.0 + 50 / 1e6 * 2.0)
    assert call.prompt_version == "planner-v3"


async def test_an_unpriced_model_records_a_null_cost():
    """Not zero. A ledger that reports an uncosted call as free reads as
    authoritative while being blind."""
    gateway, recorder = build(spec("strong", priced=False))

    await gateway.generate(role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT)

    assert recorder.calls[0].cost_usd is None
    assert recorder.total_cost_usd is None


async def test_a_run_total_refuses_to_sum_a_partially_costed_ledger():
    """A partial total is worse than no total: it looks complete."""
    gateway, recorder = build(
        spec("strong"),
        spec("cheap", ModelTier.SMALL, priced=False),
    )

    await gateway.generate(role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT)
    await gateway.generate(role=AgentName.PLANNER, mode=ResearchMode.DEEP, prompt=PROMPT)

    assert recorder.total_cost_usd is None
    assert recorder.total_tokens == 300


# --- capability enforcement ----------------------------------------------


async def test_temperature_is_dropped_for_a_model_that_rejects_it():
    """Sending it is a hard 400 on the models this project routes to. The
    caller should not have to know which."""
    provider = ScriptedProvider()
    gateway, _ = build(
        spec("strong", temperature=False),
        providers={LlmProvider.ANTHROPIC: provider},
    )

    await gateway.generate(
        role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT, temperature=0.7
    )

    assert provider.requests[0].temperature is None


async def test_temperature_is_passed_to_a_model_that_accepts_it():
    provider = ScriptedProvider()
    gateway, _ = build(spec("strong"), providers={LlmProvider.ANTHROPIC: provider})

    await gateway.generate(
        role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT, temperature=0.7
    )

    assert provider.requests[0].temperature == 0.7


async def test_output_tokens_are_clamped_to_the_model_ceiling():
    """Asking for more than the model allows is a 400, and the caller has no
    reason to know each model's limit."""
    provider = ScriptedProvider()
    gateway, _ = build(
        spec("strong", max_output_tokens=1024),
        providers={LlmProvider.ANTHROPIC: provider},
    )

    await gateway.generate(
        role=AgentName.CRITIC,
        mode=ResearchMode.DEEP,
        prompt=PROMPT,
        max_output_tokens=999_999,
    )

    assert provider.requests[0].max_output_tokens == 1024


async def test_streaming_is_refused_where_the_model_cannot_stream():
    gateway, _ = build(spec("strong", streaming=False))

    with pytest.raises(CapabilityNotSupported):
        async for _ in gateway.stream(role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT):
            pass


async def test_structured_output_skips_models_that_cannot_produce_it():
    """Rather than sending the request and failing on the response."""
    gateway, _ = build(
        spec("strong", structured=False),
        spec("medium", ModelTier.MEDIUM),
    )

    result = await gateway.generate_structured(
        role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT, schema=Answer
    )

    assert result.completion.model == "medium"


async def test_no_structured_capable_model_is_an_explicit_failure():
    gateway, _ = build(spec("strong", structured=False))

    with pytest.raises(ModelNotConfigured):
        await gateway.generate_structured(
            role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT, schema=Answer
        )


# --- retry ----------------------------------------------------------------


async def test_a_rate_limit_is_retried_on_the_same_model():
    """The failure a pause actually fixes."""
    provider = ScriptedProvider(script=[ProviderRateLimited(), "recovered"])
    gateway, recorder = build(spec("strong"), providers={LlmProvider.ANTHROPIC: provider})

    completion = await gateway.generate(
        role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT
    )

    assert completion.text == "recovered"
    assert provider.calls == 2
    # Both attempts are in the ledger. A run that burned a retry cost two calls.
    assert [call.status for call in recorder.calls] == [
        LlmCallStatus.ERROR,
        LlmCallStatus.OK,
    ]


async def test_retries_are_bounded():
    """An unbounded retry loop is the project's cardinal sin."""
    provider = ScriptedProvider(script=[ProviderRateLimited()])
    gateway, recorder = build(
        spec("strong"),
        providers={LlmProvider.ANTHROPIC: provider},
        max_attempts_per_model=2,
    )

    with pytest.raises(ProviderRateLimited):
        await gateway.generate(role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT)

    assert provider.calls == 2
    assert len(recorder.calls) == 2


async def test_a_rejected_credential_is_never_retried():
    """Retrying a 401 sixty times burns the budget and still fails."""
    provider = ScriptedProvider(script=[ProviderRejectedRequest()])
    gateway, _ = build(spec("strong"), providers={LlmProvider.ANTHROPIC: provider})

    with pytest.raises(ProviderRejectedRequest):
        await gateway.generate(role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT)

    assert provider.calls == 1


# --- failover -------------------------------------------------------------


async def test_a_provider_outage_costs_latency_not_the_run():
    down = ScriptedProvider(LlmProvider.ANTHROPIC, script=[ProviderUnavailable()])
    up = ScriptedProvider(LlmProvider.OLLAMA, script=["from the fallback"])
    gateway, recorder = build(
        spec("strong"),
        spec("local", ModelTier.MEDIUM, provider=LlmProvider.OLLAMA),
        providers={LlmProvider.ANTHROPIC: down, LlmProvider.OLLAMA: up},
    )

    completion = await gateway.generate(
        role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT
    )

    assert completion.text == "from the fallback"
    # The successful call is marked as a fallback and names what it replaced,
    # so a quality regression can be traced to the model that actually ran.
    final = recorder.calls[-1]
    assert final.status is LlmCallStatus.FALLBACK
    assert final.fell_back_from == "strong"


async def test_a_context_overflow_fails_over_without_retrying():
    """Retrying the same model with the same prompt is guaranteed to fail; a
    model with a bigger window may not."""
    small = ScriptedProvider(LlmProvider.ANTHROPIC, script=[ContextWindowExceeded()])
    large = ScriptedProvider(LlmProvider.OLLAMA, script=["fits"])
    gateway, _ = build(
        spec("strong"),
        spec("local", ModelTier.MEDIUM, provider=LlmProvider.OLLAMA),
        providers={LlmProvider.ANTHROPIC: small, LlmProvider.OLLAMA: large},
    )

    completion = await gateway.generate(
        role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT
    )

    assert completion.text == "fits"
    assert small.calls == 1, "a context overflow must not be retried on the same model"


async def test_when_every_model_fails_the_last_error_is_raised():
    down = ScriptedProvider(script=[ProviderUnavailable()])
    gateway, _ = build(
        spec("strong"),
        spec("medium", ModelTier.MEDIUM),
        providers={LlmProvider.ANTHROPIC: down},
    )

    with pytest.raises(ProviderUnavailable):
        await gateway.generate(role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT)


async def test_a_model_whose_provider_is_not_configured_says_so():
    """Names the model, so an operator can fix the configuration rather than
    debug a 401 three layers down."""
    gateway, _ = build(
        spec("openai-only", provider=LlmProvider.OPENAI),
        providers={LlmProvider.ANTHROPIC: ScriptedProvider()},
    )

    with pytest.raises(ModelNotConfigured) as raised:
        await gateway.generate(role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT)

    assert raised.value.context["model"] == "openai-only"


# --- bounds ---------------------------------------------------------------


async def test_concurrency_is_capped():
    """Fifty parallel researchers become N active calls and the rest queue
    (TDD 6.3). Without this, fan-out is a rate-limit wall."""
    active = 0
    peak = 0

    class SlowProvider(ScriptedProvider):
        async def generate(self, request: CompletionRequest) -> Completion:
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            try:
                await asyncio.sleep(0.01)
                return await super().generate(request)
            finally:
                active -= 1

    gateway, _ = build(
        spec("strong"),
        providers={LlmProvider.ANTHROPIC: SlowProvider()},
        max_concurrent_calls=3,
    )

    await asyncio.gather(
        *(
            gateway.generate(role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT)
            for _ in range(12)
        )
    )

    # Exactly 3, not "at most 3": a `<=` here would also pass if something had
    # accidentally serialised every call, which is the opposite defect.
    assert peak == 3


async def test_a_hung_provider_is_cut_off_and_classified_as_a_timeout():
    """An unbounded model call is a research run that stalls with no
    explanation."""

    class HangingProvider(ScriptedProvider):
        async def generate(self, request: CompletionRequest) -> Completion:
            await asyncio.sleep(30)
            raise AssertionError("unreachable")

    gateway, recorder = build(
        spec("strong"),
        providers={LlmProvider.ANTHROPIC: HangingProvider()},
        request_timeout_seconds=0.05,
        max_attempts_per_model=1,
    )

    from app.models import ProviderTimeout

    with pytest.raises(ProviderTimeout):
        await gateway.generate(role=AgentName.CRITIC, mode=ResearchMode.DEEP, prompt=PROMPT)

    assert recorder.calls[-1].error_code == "provider_timeout"


# --- streaming and embeddings --------------------------------------------


async def test_streaming_records_the_call_after_the_final_chunk():
    gateway, recorder = build(spec("strong"))

    chunks = [
        chunk
        async for chunk in gateway.stream(
            role=AgentName.SYNTHESIZER, mode=ResearchMode.DEEP, prompt=PROMPT
        )
    ]

    assert "".join(c.delta for c in chunks) == "ok"
    (call,) = recorder.calls
    assert call.operation == "stream"
    assert call.completion_tokens == 50


async def test_a_failed_stream_is_still_recorded():
    """A call that failed halfway still consumed tokens and time."""

    class FailingStream(ScriptedProvider):
        async def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
            yield CompletionChunk(delta="partial")
            raise ProviderUnavailable()

    gateway, recorder = build(spec("strong"), providers={LlmProvider.ANTHROPIC: FailingStream()})

    with pytest.raises(ProviderUnavailable):
        async for _ in gateway.stream(
            role=AgentName.SYNTHESIZER, mode=ResearchMode.DEEP, prompt=PROMPT
        ):
            pass

    assert recorder.calls[-1].status is LlmCallStatus.ERROR


async def test_embedding_uses_the_single_configured_model_and_is_recorded():
    provider = ScriptedProvider()
    gateway, recorder = build(
        spec("chat"),
        spec("embed", ModelTier.SMALL, chat=False, embeddings=True),
        providers={LlmProvider.ANTHROPIC: provider},
    )

    result = await gateway.embed(["one", "two"])

    assert len(result.vectors) == 2
    assert recorder.calls[-1].operation == "embed"
    assert recorder.calls[-1].model == "embed"
