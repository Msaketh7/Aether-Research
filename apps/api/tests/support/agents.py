"""Driving the Phase 10 agents: a scripted model, and states to run them against.

The model is a provider behind a **real** ``LLMGateway`` rather than a stubbed
gateway. That is deliberate: the gateway is what prices a completion, and an
agent's whole cost report comes from ``gateway.cost_of``. A stubbed gateway
would let an agent claim a cost the registry does not declare, which is the one
number in this system that must never be invented.

What the scripted provider keeps is what the assertions need: the prompts it was
given, so a test can prove that retrieved text arrived inside the untrusted
delimiters and nowhere else.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel

from app.agents.schemas import (
    ClaimItem,
    ContradictionItem,
    EvidenceItem,
    RunBudget,
    RunParameters,
    SourceRef,
    Subtask,
    SubtaskAssignment,
    TaskOutcome,
)
from app.agents.state import ResearchState, RunBrief, initial_state
from app.core.enums import (
    ClaimStatus,
    ClaimType,
    DocumentFormat,
    EvidenceStance,
    LlmProvider,
    ResearchMode,
    SourceType,
    TaskPriority,
)
from app.db.models.source import EMBEDDING_DIMENSIONS
from app.models.base import (
    Completion,
    CompletionChunk,
    CompletionRequest,
    EmbeddingResult,
    StructuredCompletion,
    StructuredT,
    TokenUsage,
)
from app.models.errors import CapabilityNotSupported, StructuredOutputInvalid
from app.models.gateway import LLMGateway
from app.models.recording import CollectingCallRecorder
from app.models.registry import ModelRegistry, ModelSpec, ModelTier, Pricing
from app.models.routing import ModelRouter
from app.retrieval.filters import ChunkView
from app.retrieval.results import ArmOutcome, RetrievalResult, RetrievalStrategy, RetrievedChunk
from app.sources.untrusted import UntrustedText

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "aether/tests/agents")

RESEARCH_ID = uuid.uuid5(NAMESPACE, "run")
USER_ID = uuid.uuid5(NAMESPACE, "user")

QUESTION = "Compare the major AI inference infrastructure providers on price and capacity."


def ident(name: str) -> uuid.UUID:
    """A stable id derived from a name, so orderings are reproducible."""
    return uuid.uuid5(NAMESPACE, name)


# --- the model ------------------------------------------------------------------


@dataclass
class ScriptedModel:
    """Answers each structured call with the next value in its script.

    ``prompts`` keeps every request, which is how a test asserts a *negative*:
    that a hostile page's text never appeared outside the delimited block.
    """

    answers: list[BaseModel] = field(default_factory=list)
    prompt_tokens: int = 120
    completion_tokens: int = 60
    prompts: list[CompletionRequest] = field(default_factory=list)
    failures: list[BaseException | None] = field(default_factory=list)

    @property
    def name(self) -> LlmProvider:
        return LlmProvider.ANTHROPIC

    @property
    def calls(self) -> int:
        return len(self.prompts)

    def system_prompts(self) -> list[str]:
        return [request.prompt.system or "" for request in self.prompts]

    def user_prompts(self) -> list[str]:
        return [message.content for request in self.prompts for message in request.prompt.messages]

    async def generate(self, request: CompletionRequest) -> Completion:
        raise CapabilityNotSupported("This scripted model only answers structured calls.")

    async def generate_structured(
        self, request: CompletionRequest, schema: type[StructuredT]
    ) -> StructuredCompletion[StructuredT]:
        index = len(self.prompts)
        self.prompts.append(request)
        if index < len(self.failures) and self.failures[index] is not None:
            raise self.failures[index]  # type: ignore[misc]
        if index >= len(self.answers):
            raise StructuredOutputInvalid(
                f"The script has no answer for call {index + 1}.",
                context={"schema": schema.__name__},
            )
        value = self.answers[index]
        if not isinstance(value, schema):
            raise StructuredOutputInvalid(
                "The script's next answer is not the schema this call asked for.",
                context={"expected": schema.__name__, "scripted": type(value).__name__},
            )
        return StructuredCompletion(value=value, completion=self._completion(request))

    async def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        raise CapabilityNotSupported("This scripted model does not stream.")
        yield  # pragma: no cover - unreachable, present to make this a generator

    async def embed(self, texts: Sequence[str], *, model: str) -> EmbeddingResult:
        raise CapabilityNotSupported("This scripted model has no embeddings.")

    async def count_tokens(self, request: CompletionRequest) -> int:
        raise CapabilityNotSupported("This scripted model cannot count tokens.")

    async def check(self) -> bool:
        return True

    async def close(self) -> None:
        return None

    def _completion(self, request: CompletionRequest) -> Completion:
        return Completion(
            text="{}",
            provider=self.name,
            model=request.model,
            usage=TokenUsage(
                prompt_tokens=self.prompt_tokens, completion_tokens=self.completion_tokens
            ),
            finish_reason="stop",
            latency_ms=7,
        )


def registry(*, priced: bool = True) -> ModelRegistry:
    """One chat model per tier, so every role resolves, plus one for embeddings.

    The embedding model is declared because assembling the agents also assembles
    ingestion and retrieval, and both resolve it when they are built. Its width
    matches the vector column, as ADR 0012 requires of a real deployment.
    """
    pricing = Pricing(1.0, 2.0, dt.date(2026, 1, 1), "test") if priced else None
    specs = {
        tier.value: ModelSpec(
            key=tier.value,
            provider=LlmProvider.ANTHROPIC,
            model_id=f"scripted-{tier.value}",
            tier=tier,
            context_window=200_000,
            max_output_tokens=32_000,
            pricing=pricing,
        )
        for tier in ModelTier
    }
    specs["embed"] = ModelSpec(
        key="embed",
        provider=LlmProvider.ANTHROPIC,
        model_id="scripted-embed",
        tier=ModelTier.SMALL,
        context_window=8192,
        max_output_tokens=1,
        supports_chat=False,
        supports_streaming=False,
        supports_structured_output=False,
        supports_embeddings=True,
        embedding_dimensions=EMBEDDING_DIMENSIONS,
        pricing=pricing,
    )
    return ModelRegistry(specs=specs)


def gateway(
    *answers: BaseModel,
    priced: bool = True,
    failures: Sequence[BaseException | None] = (),
) -> tuple[LLMGateway, ScriptedModel, CollectingCallRecorder]:
    """A real gateway over a scripted provider, plus the call ledger."""
    model = ScriptedModel(answers=list(answers), failures=list(failures))
    recorder = CollectingCallRecorder()
    declared = registry(priced=priced)
    return (
        LLMGateway(
            registry=declared,
            router=ModelRouter(declared),
            providers={LlmProvider.ANTHROPIC: model},
            recorder=recorder,
            max_attempts_per_model=1,
        ),
        model,
        recorder,
    )


# --- retrieval ------------------------------------------------------------------


@dataclass
class ScriptedRetriever:
    """Returns the chunks it was given, recording what it was asked.

    A stand-in for ``PostgresRetriever``, which has its own tests against a real
    database. What the agent tests need from retrieval is which chunks came back
    and under which filter, not the SQL that found them.
    """

    chunks: list[RetrievedChunk] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    filters: list[object] = field(default_factory=list)
    skipped_dense: bool = False

    async def retrieve(
        self, query: str, *, run_id: uuid.UUID, user_id: uuid.UUID, limit: int | None = None
    ) -> RetrievalResult:
        self.queries.append(query)
        return self._result(limit)

    async def retrieve_with_filters(
        self,
        query: str,
        *,
        filters: object,
        user_id: uuid.UUID,
        limit: int | None = None,
    ) -> RetrievalResult:
        """Honours ``source_ids``, because callers filter by it and read the result.

        The real retriever narrows to the sources it is given. A double that
        ignored the filter would let a test pass while the caller's filter did
        nothing.
        """
        self.queries.append(query)
        self.filters.append(filters)
        wanted = set(getattr(filters, "source_ids", ()) or ())
        if not wanted:
            return self._result(limit)
        return self._result(limit, only=wanted)

    async def retrieve_hybrid(
        self, query: str, *, filters: object, user_id: uuid.UUID, plan: object | None = None
    ) -> RetrievalResult:
        self.queries.append(query)
        self.filters.append(filters)
        return self._result(None)

    def _result(self, limit: int | None, *, only: set[uuid.UUID] | None = None) -> RetrievalResult:
        available = [hit for hit in self.chunks if only is None or hit.chunk.source_id in only]
        chunks = tuple(available[: limit or len(available)])
        arms = [
            ArmOutcome(
                strategy=RetrievalStrategy.LEXICAL, ran=True, returned=len(chunks), latency_ms=1
            ),
            ArmOutcome(
                strategy=RetrievalStrategy.DENSE,
                ran=not self.skipped_dense,
                returned=0 if self.skipped_dense else len(chunks),
                latency_ms=1,
                skipped_reason="no embedding model is configured" if self.skipped_dense else None,
            ),
        ]
        return RetrievalResult(
            strategy=RetrievalStrategy.HYBRID,
            chunks=chunks,
            arms=tuple(arms),
            candidates=len(chunks),
            reranker=None,
            latency_ms=2,
        )


def chunk(
    name: str,
    text: str,
    *,
    source: str = "source-a",
    source_id: uuid.UUID | None = None,
    document: str = "doc-a",
    char_start: int = 0,
    url: str = "https://example.test/a",
) -> RetrievedChunk:
    """A retrieved chunk. ``source_id`` names a source that really exists in a
    database; ``source`` derives one for the tests that need no rows."""
    view = ChunkView(
        id=ident(name),
        document_id=ident(document),
        source_id=source_id or ident(source),
        source_type=SourceType.WEB,
        chunk_index=0,
        text=UntrustedText(text, source_url=url),
        token_count=max(1, len(text.split())),
        char_start=char_start,
        char_end=char_start + len(text),
        page_start=None,
        page_end=None,
        section=None,
        format=DocumentFormat.HTML,
        language="en",
        embedded=True,
    )
    return RetrievedChunk(chunk=view, score=1.0, fusion_score=1.0, lexical_rank=1)


# --- states ---------------------------------------------------------------------


def budget(**overrides: object) -> RunBudget:
    values: dict[str, object] = {
        "max_iterations": 3,
        "max_sources": 20,
        "max_search_queries": 12,
        "max_runtime_seconds": 300,
        "max_cost_usd": 2.0,
    }
    values.update(overrides)
    return RunBudget.model_validate(values)


def brief(**overrides: object) -> RunBrief:
    values: dict[str, object] = {
        "research_id": RESEARCH_ID,
        "user_id": USER_ID,
        "query": QUESTION,
        "mode": ResearchMode.DEEP,
        "depth": 3,
        "budget": budget(),
    }
    values.update(overrides)
    return RunBrief.model_validate(values)


def state(**overrides: object) -> ResearchState:
    """A run's state, built from a brief and then overridden field by field."""
    base = initial_state(brief(), now=dt.datetime(2026, 9, 1, tzinfo=dt.UTC))
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


def parameters(**overrides: object) -> RunParameters:
    values: dict[str, object] = {"mode": ResearchMode.DEEP, "depth": 3}
    values.update(overrides)
    return RunParameters.model_validate(values)


def subtask(
    key: str = "i1-1",
    *,
    question: str = "What does each provider charge per GPU-hour?",
    iteration: int = 1,
    priority: TaskPriority = TaskPriority.HIGH,
    **overrides: object,
) -> Subtask:
    values: dict[str, object] = {
        "key": key,
        "question": question,
        "iteration": iteration,
        "priority": priority,
    }
    values.update(overrides)
    return Subtask.model_validate(values)


def assignment(**overrides: object) -> SubtaskAssignment:
    values: dict[str, object] = {
        "research_id": RESEARCH_ID,
        "user_id": USER_ID,
        "query": QUESTION,
        "mode": ResearchMode.DEEP,
        "subtask": subtask(),
        "query_allowance": 3,
        "source_allowance": 4,
        "time_allowance_seconds": 120.0,
    }
    values.update(overrides)
    return SubtaskAssignment.model_validate(values)


def source(name: str = "source-a", *, task_key: str = "i1-1", title: str = "A page") -> SourceRef:
    return SourceRef(
        source_id=ident(name),
        task_key=task_key,
        title=title,
        url=f"https://example.test/{name}",
    )


def outcome(task_key: str = "i1-1", *, iteration: int = 1, sources: tuple[SourceRef, ...] = ()):
    return TaskOutcome(task_key=task_key, iteration=iteration, sources=sources)


def evidence(
    name: str = "ev-1",
    *,
    quote: str = "Inference on H100 instances is priced at $4.10 per GPU-hour.",
    task_key: str = "i1-1",
    source_name: str = "source-a",
    document_name: str = "doc-a",
    span_start: int = 0,
    stance: EvidenceStance = EvidenceStance.SUPPORTS,
    model: str = "test-extractor-v1",
) -> EvidenceItem:
    return EvidenceItem(
        id=ident(name),
        task_key=task_key,
        iteration=1,
        source_id=ident(source_name),
        document_id=ident(document_name),
        claim_text=quote,
        span_start=span_start,
        span_end=span_start + len(quote),
        stance=stance,
        extractor_model=model,
    )


def claim(
    name: str = "claim-1",
    *,
    text: str = "H100 inference costs $4.10 per GPU-hour.",
    key: str = "provider a | h100 price per gpu hour | 2026",
    evidence_ids: tuple[uuid.UUID, ...] = (),
    status: ClaimStatus = ClaimStatus.CANDIDATE,
    confidence: float = 0.6,
    object_value: str = "$4.10",
    claim_id: uuid.UUID | None = None,
) -> ClaimItem:
    return ClaimItem(
        id=claim_id or ident(name),
        normalized_key=key,
        text=text,
        claim_type=ClaimType.QUANTITATIVE,
        status=status,
        confidence=confidence,
        evidence_ids=evidence_ids or (ident("ev-1"),),
        object_value=object_value,
    )


def contradiction(
    name: str = "c-1",
    *,
    key: str = "provider a | h100 price per gpu hour | 2026",
    a: str = "claim-1",
    b: str = "claim-2",
    reason: str = "different fiscal periods",
) -> ContradictionItem:
    return ContradictionItem(
        id=ident(name),
        normalized_key=key,
        claim_a_id=ident(a),
        claim_b_id=ident(b),
        likely_reason=reason,
    )
