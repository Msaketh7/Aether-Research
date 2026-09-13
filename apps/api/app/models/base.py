"""The ``LLMProvider`` interface and the value types that cross it.

**Not the ORM.** ``app.db.models`` holds SQLAlchemy rows; this package is the
LLM gateway, model registry and routing, which is where ``docs/TDD.md`` section
6 puts them. The two are never imported into each other.

The interface is the lowest common denominator across three providers that do
not agree on much (ADR 0007). Everything provider-specific is either erased here
or promoted to an explicit capability flag on the model spec - never left as an
undocumented assumption that happens to hold for whichever provider was
configured when the code was written. Two examples that are already load-bearing:

* Anthropic has **no embeddings API at all**, so ``embed`` on that provider
  raises ``CapabilityNotSupported`` rather than returning a plausible-looking
  empty vector.
* Current Anthropic models **reject** ``temperature`` with a 400. So temperature
  is a capability, not a parameter every provider quietly accepts.

Getting either wrong produces a system that works until the router picks a
different model, which is the failure mode an abstraction like this exists to
prevent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from app.core.enums import LlmProvider

#: A structured-output schema. Callers pass the model class; the provider gets
#: the JSON schema from it and the gateway validates the response back into it,
#: so a malformed generation fails here rather than three layers downstream.
StructuredT = TypeVar("StructuredT", bound=BaseModel)


class MessageRole(StrEnum):
    """Who authored a message.

    Distinct from ``AgentName``, which is *which agent is making the call*. The
    collision of the word "role" between the two is unavoidable - both are
    industry terms - so the types are kept far apart and never interchanged.
    """

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: MessageRole
    content: str


@dataclass(frozen=True, slots=True)
class Prompt:
    """What is sent to a model.

    The system instruction is a separate field rather than a first message
    because that is how Anthropic models it, and flattening it into the message
    list would lose information that provider needs. Providers that want it
    inline can put it back; the reverse is not recoverable.

    ``version`` is written to every ``llm_calls`` row, so a change in output
    quality can be attributed to a prompt edit rather than guessed at
    (TDD 7.2). Phase 10 supplies real versions from ``packages/prompts``.
    """

    messages: Sequence[ChatMessage]
    system: str | None = None
    version: str = "unversioned"

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("A prompt needs at least one message.")


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """What a call consumed.

    Zero is a real measurement here, not a placeholder: a provider that does not
    report usage must say so by raising, not by reporting zero tokens, or cost
    governance in Phase 16 silently under-counts.
    """

    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True, slots=True)
class Completion:
    """A finished generation."""

    text: str
    provider: LlmProvider
    model: str
    usage: TokenUsage
    #: Provider-native reason, normalised where the providers agree:
    #: ``stop`` | ``length`` | ``refusal`` | ``tool_use`` | ``other``.
    finish_reason: str
    latency_ms: int
    #: The provider's own request id, for correlating with their support.
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class StructuredCompletion[T: BaseModel]:
    """A generation already validated against the caller's schema."""

    value: T
    completion: Completion


@dataclass(frozen=True, slots=True)
class CompletionChunk:
    """One piece of a streamed response.

    The final chunk carries ``usage`` and ``finish_reason`` and an empty delta.
    Streaming without a terminal usage report would make every streamed call
    invisible to cost accounting, which is the thing the gateway exists to
    prevent.
    """

    delta: str
    usage: TokenUsage | None = None
    finish_reason: str | None = None

    @property
    def is_final(self) -> bool:
        return self.usage is not None or self.finish_reason is not None


class EmbeddingPurpose(StrEnum):
    """What an embedding is for, which for some models changes the input.

    Asymmetric embedding models - ``nomic-embed-text`` among them - are trained
    with a task prefix on every input, a different one for the passage being
    stored and for the question being asked. Omitting them does not fail: it
    quietly puts queries and documents in slightly different places in the same
    space, and retrieval simply gets worse. The prefixes are declared per model
    in the registry and applied by the gateway, so no caller has to know which
    models need them.
    """

    #: A passage being indexed.
    DOCUMENT = "document"
    #: A question being asked of the index.
    QUERY = "query"


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    vectors: Sequence[Sequence[float]]
    provider: LlmProvider
    model: str
    usage: TokenUsage
    latency_ms: int

    @property
    def dimensions(self) -> int:
        return len(self.vectors[0]) if self.vectors else 0


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    """One call's parameters, independent of provider.

    ``max_output_tokens`` has no default: Anthropic requires it, and a silent
    default is how a synthesis step gets truncated at 1024 tokens in production
    without anyone noticing. The caller states it or the registry's per-model
    ceiling is used explicitly.
    """

    model: str
    prompt: Prompt
    max_output_tokens: int
    #: Applied only when the model declares ``supports_temperature``; see the
    #: module docstring. Ignored with a warning rather than sent and rejected.
    temperature: float | None = None
    stop: Sequence[str] = field(default_factory=tuple)
    #: Reasoning depth where the provider exposes it. Anthropic maps this to
    #: ``output_config.effort``; providers without the concept ignore it.
    effort: str | None = None
    timeout_seconds: float | None = None


@runtime_checkable
class LLMProvider(Protocol):
    """One provider's implementation of the four required operations.

    Agents never construct one of these (ADR 0007). They ask the gateway for a
    role; the gateway owns provider selection, bounds and accounting. That is
    what makes cost-versus-quality a configuration decision instead of a string
    literal buried in an agent node.
    """

    @property
    def name(self) -> LlmProvider: ...

    async def generate(self, request: CompletionRequest) -> Completion:
        """Produce a completion. Raises from ``app.models.errors``."""
        ...

    async def generate_structured(
        self,
        request: CompletionRequest,
        schema: type[StructuredT],
    ) -> StructuredCompletion[StructuredT]:
        """Produce a completion validated against ``schema``.

        Uses the provider's native constrained decoding where it has one, and
        validates the result regardless: "the provider promised valid JSON" is
        not the same as "this parsed into the type the caller asked for".
        """
        ...

    def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        """Yield the response incrementally, ending with a usage-bearing chunk."""
        ...

    async def embed(self, texts: Sequence[str], *, model: str) -> EmbeddingResult:
        """Embed texts. Raises ``CapabilityNotSupported`` where there is no API."""
        ...

    async def count_tokens(self, request: CompletionRequest) -> int:
        """Count a prompt's tokens without generating.

        Raises ``CapabilityNotSupported`` rather than estimating: a wrong token
        count that looks right is worse than an honest refusal, because Phase 16
        will make budget decisions with it.
        """
        ...

    async def check(self) -> bool:
        """Is the provider reachable and are its credentials accepted?"""
        ...

    async def close(self) -> None: ...
