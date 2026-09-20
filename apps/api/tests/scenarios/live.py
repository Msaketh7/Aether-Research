"""Shared wiring for the opt-in live tests.

Two of them, on two different axes, and they are separate because they fail for
different reasons: `test_live_model` replaces the scripted *model* and keeps the
scripted socket, `test_live_web` replaces both and goes to the open internet.
What they have in common is the gateway below.

Nothing here runs unless a test asks for it, and every live test is skipped
without an explicit opt-in, because all of them spend money.
"""

from __future__ import annotations

import datetime as dt
import os

from app.core.config import Settings
from app.core.enums import LlmProvider
from app.models import ModelRegistry, ModelRouter, ModelSpec, ModelTier
from app.models.gateway import LLMGateway
from app.models.registry import Pricing
from tests.support import agents as fake

#: Cheap, fast, and structured-output capable. Overridable because the point of
#: these tests is to try a real model, and which one is a question worth asking.
MODEL_ID = os.environ.get("AETHER_LIVE_MODEL_ID", "gpt-4o-mini")


def real_gateway(brain: object, *, settings: Settings, budget: object) -> LLMGateway:
    """The real chat provider, wired exactly as the worker wires the scripted one.

    One real model serves every tier. The tiers exist to trade cost against
    quality, and collapsing them keeps a run cheap while still exercising every
    role's prompt and parser.

    Embeddings stay scripted. The hosted embedding models are wider than the 768
    the column declares, which ``ChunkEmbedder`` correctly refuses, and the dense
    arm needs pgvector regardless - so a real one here would buy nothing and cost
    the run its ingestion.
    """
    from app.models.providers.openai import OpenAIProvider

    chat = {
        tier.value: ModelSpec(
            key=tier.value,
            provider=LlmProvider.OPENAI,
            model_id=MODEL_ID,
            tier=tier,
            context_window=128_000,
            max_output_tokens=16_384,
            # A placeholder price, so the ledger has arithmetic to do and the
            # budget guard is exercised. Not a claim about what the vendor
            # charges - see the registry's pricing rules.
            pricing=Pricing(1.0, 2.0, dt.date(2026, 9, 19), "placeholder, not a vendor price"),
        )
        for tier in ModelTier
    }
    scripted = fake.registry(priced=True)
    declared = ModelRegistry(specs={**chat, "embed": scripted.specs["embed"]})

    return LLMGateway(
        registry=declared,
        router=ModelRouter(declared, embedding_model_key="embed"),
        providers={
            LlmProvider.OPENAI: OpenAIProvider(
                api_key=os.environ["OPENAI_API_KEY"],
                timeout_seconds=settings.llm_request_timeout_seconds,
            ),
            LlmProvider.ANTHROPIC: brain,
        },
        # The guard is both recorder and budget, exactly as the worker wires it:
        # that is what enforces a ceiling before a call rather than noticing it
        # after the node that made it returned.
        recorder=budget,
        budget=budget,
        max_concurrent_calls=settings.llm_max_concurrent_calls,
        max_attempts_per_model=settings.llm_max_attempts,
        request_timeout_seconds=settings.llm_request_timeout_seconds,
        retry_base_delay_seconds=settings.llm_retry_base_delay_seconds,
        retry_max_delay_seconds=settings.llm_retry_max_delay_seconds,
    )


def search_provider_key() -> tuple[str, str] | None:
    """The first declared search credential, as (setting name, value)."""
    for name in ("TAVILY_API_KEY", "BRAVE_API_KEY"):
        value = os.environ.get(name)
        if value:
            return name.lower(), value
    return None
