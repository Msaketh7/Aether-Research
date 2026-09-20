"""The LLM gateway, model registry and routing (ADR 0007, TDD section 6).

**This is not the ORM.** ``app.db.models`` holds SQLAlchemy rows. This package
holds the one door every model call goes through, which is where ``docs/TDD.md``
puts it. The two never import each other.

Import ``LLMGateway`` and ``build_gateway``. An agent node importing
``AnthropicProvider`` directly is the failure this package exists to prevent:
cost-versus-quality has to stay a configuration decision, and it stops being one
the moment a model name is written into a node.
"""

from __future__ import annotations

from app.cache import ResponseCache
from app.core.config import Settings
from app.core.enums import LlmProvider
from app.core.logging import get_logger
from app.models.base import (
    ChatMessage,
    Completion,
    CompletionChunk,
    CompletionRequest,
    EmbeddingPurpose,
    EmbeddingResult,
    LLMProvider,
    MessageRole,
    Prompt,
    StructuredCompletion,
    TokenUsage,
)
from app.models.budget import (
    BudgetExhausted,
    BudgetGuard,
    ModelNotPriced,
    NullBudgetGuard,
    RunBudgetGuard,
    RunSpend,
)
from app.models.errors import (
    CapabilityNotSupported,
    ContextWindowExceeded,
    ModelError,
    ModelNotConfigured,
    ProviderRateLimited,
    ProviderRefused,
    ProviderRejectedRequest,
    ProviderTimeout,
    ProviderUnavailable,
    StructuredOutputInvalid,
)
from app.models.gateway import LLMGateway
from app.models.limiter import ConcurrencyLimiter, Saturation, SlotObserver
from app.models.providers import AnthropicProvider, OllamaProvider, OpenAIProvider
from app.models.recording import (
    CallRecorder,
    CollectingCallRecorder,
    LlmCallRecord,
    LoggingCallRecorder,
)
from app.models.registry import ModelRegistry, ModelSpec, ModelTier, Pricing, load_registry
from app.models.routing import ModelRouter, RoutingDecision

logger = get_logger(__name__)


def build_providers(settings: Settings) -> dict[LlmProvider, LLMProvider]:
    """Construct every provider the configuration has credentials for.

    A provider without a key is **absent**, not a stub that fails at call time.
    The registry can then declare models for it and the router will report
    "provider not configured" naming the model - a message an operator can act
    on - rather than a 401 from three layers down.

    Ollama needs no key at all, which is the point of it: a developer with
    ``ollama serve`` running gets a working graph without an account anywhere.
    """
    providers: dict[LlmProvider, LLMProvider] = {}

    if settings.anthropic_api_key is not None:
        providers[LlmProvider.ANTHROPIC] = AnthropicProvider(
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout_seconds=settings.llm_request_timeout_seconds,
        )
    if settings.openai_api_key is not None:
        providers[LlmProvider.OPENAI] = OpenAIProvider(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout_seconds=settings.llm_request_timeout_seconds,
        )
    if settings.ollama_base_url:
        providers[LlmProvider.OLLAMA] = OllamaProvider(
            base_url=settings.ollama_base_url,
            timeout_seconds=settings.llm_request_timeout_seconds,
        )

    logger.info(
        "llm providers configured",
        extra={"providers": sorted(name.value for name in providers)},
    )
    return providers


def build_gateway(
    settings: Settings,
    *,
    providers: dict[LlmProvider, LLMProvider] | None = None,
    recorder: CallRecorder | None = None,
    cache: ResponseCache | None = None,
    budget: BudgetGuard | None = None,
    slot_observer: SlotObserver | None = None,
) -> LLMGateway:
    """Assemble the gateway from configuration.

    Called once per process. Building one per request would give each request
    its own concurrency semaphore, which is the same as having none.
    """
    registry = load_registry(settings.model_registry_path)
    router = ModelRouter(registry, embedding_model_key=settings.embedding_model)

    return LLMGateway(
        registry=registry,
        router=router,
        providers=providers if providers is not None else build_providers(settings),
        recorder=recorder or LoggingCallRecorder(),
        cache=cache,
        budget=budget,
        max_concurrent_calls=settings.llm_max_concurrent_calls,
        max_attempts_per_model=settings.llm_max_attempts,
        request_timeout_seconds=settings.llm_request_timeout_seconds,
        retry_base_delay_seconds=settings.llm_retry_base_delay_seconds,
        retry_max_delay_seconds=settings.llm_retry_max_delay_seconds,
        slot_observer=slot_observer,
    )


__all__ = [
    "AnthropicProvider",
    "BudgetExhausted",
    "BudgetGuard",
    "CallRecorder",
    "CapabilityNotSupported",
    "ChatMessage",
    "CollectingCallRecorder",
    "Completion",
    "CompletionChunk",
    "CompletionRequest",
    "ConcurrencyLimiter",
    "ContextWindowExceeded",
    "EmbeddingPurpose",
    "EmbeddingResult",
    "LLMGateway",
    "LLMProvider",
    "LlmCallRecord",
    "LoggingCallRecorder",
    "MessageRole",
    "ModelError",
    "ModelNotConfigured",
    "ModelNotPriced",
    "ModelRegistry",
    "ModelRouter",
    "ModelSpec",
    "ModelTier",
    "NullBudgetGuard",
    "OllamaProvider",
    "OpenAIProvider",
    "Pricing",
    "Prompt",
    "ProviderRateLimited",
    "ProviderRefused",
    "ProviderRejectedRequest",
    "ProviderTimeout",
    "ProviderUnavailable",
    "RoutingDecision",
    "RunBudgetGuard",
    "RunSpend",
    "Saturation",
    "SlotObserver",
    "StructuredCompletion",
    "StructuredOutputInvalid",
    "TokenUsage",
    "build_gateway",
    "build_providers",
    "load_registry",
]
