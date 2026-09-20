"""The model registry and the routing policy.

Two things are being protected.

The **cost arithmetic**, because Phase 16 will refuse to start work on the
strength of it and Phase 18 will publish it. In particular the difference
between a model that costs nothing and a model whose price is unknown: one is a
measurement, the other is the absence of one, and a system that conflates them
reports a run as free when it has no idea what it spent.

The **routing policy**, because it is the knob that trades cost against quality
across the whole product. It is data, so it must be asserted like data.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from pathlib import Path

import pytest

from app.core.enums import AgentName, LlmProvider, ResearchMode
from app.models import ModelNotConfigured, ModelRegistry, ModelRouter, ModelSpec, ModelTier
from app.models.base import TokenUsage
from app.models.registry import Pricing, load_registry

USAGE = TokenUsage(prompt_tokens=1_000_000, completion_tokens=1_000_000)


def spec(
    key: str,
    tier: ModelTier,
    *,
    provider: LlmProvider = LlmProvider.ANTHROPIC,
    priced: bool = True,
    chat: bool = True,
    embeddings: bool = False,
) -> ModelSpec:
    return ModelSpec(
        key=key,
        provider=provider,
        model_id=key,
        tier=tier,
        context_window=100_000,
        max_output_tokens=4096,
        pricing=(
            Pricing(
                input_usd_per_mtok=1.0,
                output_usd_per_mtok=2.0,
                as_of=dt.date(2026, 1, 1),
                source="test",
            )
            if priced
            else None
        ),
        supports_chat=chat,
        supports_embeddings=embeddings,
    )


def registry(*specs: ModelSpec) -> ModelRegistry:
    return ModelRegistry(specs={s.key: s for s in specs})


# --- the shipped registry -------------------------------------------------


def test_the_shipped_registry_loads():
    """A registry that will not parse is a process that will not start."""
    loaded = load_registry(None)

    assert len(loaded) > 0
    assert LlmProvider.ANTHROPIC in loaded.providers()
    assert LlmProvider.OLLAMA in loaded.providers()


def test_every_shipped_model_is_priced_or_visibly_unpriced():
    """Not an assertion that everything has a price - an assertion that a price
    is either real and dated, or explicitly absent. Never invented."""
    for model in load_registry(None):
        if model.pricing is None:
            assert model.cost_usd(USAGE) is None
        else:
            assert model.pricing.source
            assert isinstance(model.pricing.as_of, dt.date)


def test_the_shipped_registry_declares_a_local_model():
    """ADR 0007's promise: the graph runs with no API key at all."""
    local = [m for m in load_registry(None) if m.provider is LlmProvider.OLLAMA]

    assert local, "Ollama models are what keep local development free of keys"
    assert any(m.supports_chat for m in local)
    assert any(m.supports_embeddings for m in local)


def test_a_malformed_entry_fails_loudly(tmp_path: Path):
    """Silently dropping an unparseable model turns a typo into a routing
    failure a long way from its cause."""
    bad = tmp_path / "registry.yaml"
    bad.write_text("models:\n  - key: broken\n    provider: nonsense\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid model entry"):
        load_registry(bad)


def test_duplicate_keys_are_rejected(tmp_path: Path):
    entry = (
        "  - key: dup\n    provider: ollama\n    model_id: m\n    tier: small\n"
        "    context_window: 10\n    max_output_tokens: 10\n"
    )
    path = tmp_path / "registry.yaml"
    path.write_text("models:\n" + entry + entry, encoding="utf-8")

    with pytest.raises(ValueError, match="Duplicate model key"):
        load_registry(path)


# --- cost -----------------------------------------------------------------


def test_cost_is_computed_from_the_declared_price():
    model = spec("m", ModelTier.MEDIUM)

    # 1M prompt tokens at $1 + 1M completion tokens at $2.
    assert model.cost_usd(USAGE) == 3.0


def test_an_unpriced_model_costs_none_not_zero():
    """The whole reason `cost_usd` is nullable. Reporting an unpriced call as
    $0.00 makes a budget ledger read as authoritative when it is blind."""
    model = spec("m", ModelTier.MEDIUM, priced=False)

    assert model.cost_usd(USAGE) is None
    assert model.is_priced is False


def test_a_genuinely_free_model_costs_zero():
    """The other side of the same distinction: self-hosted inference really is
    zero, and it says so with a source."""
    free = ModelSpec(
        key="local",
        provider=LlmProvider.OLLAMA,
        model_id="local",
        tier=ModelTier.SMALL,
        context_window=1000,
        max_output_tokens=100,
        pricing=Pricing(0.0, 0.0, dt.date(2026, 1, 1), "self-hosted"),
    )

    assert free.cost_usd(USAGE) == 0.0
    assert free.is_priced is True


def test_unpriced_models_are_reported_for_an_operator():
    declared = registry(spec("a", ModelTier.SMALL), spec("b", ModelTier.SMALL, priced=False))

    assert [m.key for m in declared.unpriced()] == ["b"]


# --- routing policy -------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (AgentName.PLANNER, ModelTier.SMALL),
        (AgentName.CLAIM_NORMALIZER, ModelTier.SMALL),
        (AgentName.RESEARCHER, ModelTier.MEDIUM),
        (AgentName.EVIDENCE_EXTRACTOR, ModelTier.MEDIUM),
        (AgentName.VERIFIER, ModelTier.STRONG),
        (AgentName.CRITIC, ModelTier.STRONG),
        (AgentName.SYNTHESIZER, ModelTier.STRONGEST),
    ],
)
def test_deep_mode_uses_the_declared_tier_for_each_role(role, expected):
    """The policy from ADR 0007: cheap work to cheap models, judgement to
    strong ones, the final report to the strongest."""
    router = ModelRouter(registry(spec("m", ModelTier.SMALL)))

    assert router.tier_for(role, ResearchMode.DEEP) is expected


def test_quick_mode_steps_a_role_down_a_tier():
    """Mode is where the depth-for-cost trade is actually made."""
    router = ModelRouter(registry(spec("m", ModelTier.SMALL)))

    assert router.tier_for(AgentName.CRITIC, ResearchMode.DEEP) is ModelTier.STRONG
    assert router.tier_for(AgentName.CRITIC, ResearchMode.QUICK) is ModelTier.MEDIUM


def test_the_synthesizer_never_drops_below_strong():
    """The one degradation a reader sees directly."""
    router = ModelRouter(registry(spec("m", ModelTier.SMALL)))

    for mode in ResearchMode:
        assert router.tier_for(AgentName.SYNTHESIZER, mode).rank >= ModelTier.STRONG.rank


def test_no_role_can_be_shifted_below_the_bottom_of_the_ladder():
    router = ModelRouter(registry(spec("m", ModelTier.SMALL)))

    assert router.tier_for(AgentName.PLANNER, ResearchMode.QUICK) is ModelTier.SMALL


# --- resolution and fallbacks ---------------------------------------------


def test_declaration_order_is_the_preference_order():
    """How an operator says "prefer this provider" without learning a scoring
    rule."""
    router = ModelRouter(
        registry(
            spec("first", ModelTier.STRONG),
            spec("second", ModelTier.STRONG, provider=LlmProvider.OPENAI),
        )
    )

    decision = router.resolve(role=AgentName.CRITIC, mode=ResearchMode.DEEP)

    assert decision.primary.key == "first"
    assert decision.fallbacks[0].key == "second"


def test_the_chain_falls_back_to_cheaper_tiers():
    router = ModelRouter(
        registry(
            spec("strong", ModelTier.STRONG),
            spec("medium", ModelTier.MEDIUM),
            spec("small", ModelTier.SMALL),
        )
    )

    decision = router.resolve(role=AgentName.CRITIC, mode=ResearchMode.DEEP)

    assert [m.key for m in decision.chain] == ["strong", "medium", "small"]


def test_strongest_resolves_to_the_strongest_actually_configured():
    """The build plan says "the strongest configured". With nothing declared at
    `strongest`, resolving downward would hand the final report the cheapest
    model in the registry - the exact opposite of the instruction."""
    router = ModelRouter(registry(spec("small", ModelTier.SMALL), spec("strong", ModelTier.STRONG)))

    decision = router.resolve(role=AgentName.SYNTHESIZER, mode=ResearchMode.DEEP)

    assert decision.tier is ModelTier.STRONGEST
    assert decision.primary.key == "strong"


def test_an_embeddings_model_is_never_a_chat_fallback():
    """A failover that lands on an embeddings model produces a confusing 400
    instead of an answer."""
    router = ModelRouter(
        registry(
            spec("chat", ModelTier.SMALL),
            spec("embed", ModelTier.SMALL, chat=False, embeddings=True),
        )
    )

    decision = router.resolve(role=AgentName.PLANNER, mode=ResearchMode.DEEP)

    assert "embed" not in [m.key for m in decision.chain]


def test_the_fallback_chain_is_bounded():
    """An unbounded failover walk is a slower outage that costs more."""
    router = ModelRouter(
        registry(*(spec(f"m{i}", ModelTier.STRONG) for i in range(10))),
        max_fallbacks=2,
    )

    decision = router.resolve(role=AgentName.CRITIC, mode=ResearchMode.DEEP)

    assert len(decision.chain) == 3


def test_an_empty_registry_says_what_is_missing():
    router = ModelRouter(registry())

    with pytest.raises(ModelNotConfigured) as raised:
        router.resolve(role=AgentName.PLANNER, mode=ResearchMode.DEEP)

    assert raised.value.context["role"] == "planner"


def test_the_embedding_model_is_a_single_choice():
    """Vectors from different models are not comparable, so this must not vary
    by caller or an index stops being searchable."""
    router = ModelRouter(
        registry(
            spec("chat", ModelTier.SMALL),
            spec("embed", ModelTier.SMALL, chat=False, embeddings=True),
        )
    )

    assert router.embedding_model().key == "embed"


def test_no_embedding_model_is_an_explicit_failure():
    router = ModelRouter(registry(spec("chat", ModelTier.SMALL)))

    with pytest.raises(ModelNotConfigured):
        router.embedding_model()


def test_a_dated_snapshot_is_priced_as_the_alias_it_resolved_from():
    """Found by the first live call this repository ever made.

    A provider resolves an alias to a dated snapshot and reports *that* in the
    response: ask for `gpt-4o-mini` and the completion says
    `gpt-4o-mini-2024-07-18`. Pricing looks the returned id up in the registry,
    so an exact match missed and every call to a correctly declared, correctly
    priced model was recorded as uncosted - which under a run's cost ceiling
    stops discovery at the end of the round and reports the spend as *not
    measured*.

    No scripted provider could catch this: a fake echoes back the model id it
    was handed, so the returned id and the declared id always agreed.
    """
    declared = ModelSpec(
        key="openai:small",
        provider=LlmProvider.OPENAI,
        model_id="gpt-4o-mini",
        tier=ModelTier.SMALL,
        context_window=128_000,
        max_output_tokens=16_384,
        pricing=Pricing(
            input_usd_per_mtok=1.0,
            output_usd_per_mtok=2.0,
            as_of=dt.date(2026, 1, 1),
            source="test",
        ),
    )
    known = ModelRegistry(specs={declared.key: declared})

    assert known.find(LlmProvider.OPENAI, "gpt-4o-mini") is declared
    assert known.find(LlmProvider.OPENAI, "gpt-4o-mini-2024-07-18") is declared
    # Anthropic's suffix has no separators, and is the same idea.
    assert known.find(LlmProvider.OPENAI, "gpt-4o-mini-20240718") is declared


def test_a_snapshot_never_matches_a_different_model_that_is_a_prefix_of_it():
    """`gpt-4o` is a prefix of `gpt-4o-mini`, so a plain `startswith` would
    price a mini call at the full model's rate - turning an under-report into an
    over-report, which is not an improvement. Only a bare date may follow."""
    openai = {"provider": LlmProvider.OPENAI}
    big = replace(spec("openai:strong", ModelTier.STRONG, **openai), model_id="gpt-4o")
    small = replace(spec("openai:small", ModelTier.SMALL, **openai), model_id="gpt-4o-mini")

    both = ModelRegistry(specs={big.key: big, small.key: small})

    assert both.find(LlmProvider.OPENAI, "gpt-4o-mini-2024-07-18") is small
    assert both.find(LlmProvider.OPENAI, "gpt-4o-2024-08-06") is big
    # Not a snapshot of anything declared.
    assert both.find(LlmProvider.OPENAI, "gpt-4o-audio-preview") is None


def test_a_snapshot_of_one_provider_is_not_a_snapshot_of_another():
    anthropic_spec = replace(
        spec("a", ModelTier.SMALL, provider=LlmProvider.ANTHROPIC), model_id="shared-name"
    )
    known = ModelRegistry(specs={anthropic_spec.key: anthropic_spec})

    assert known.find(LlmProvider.ANTHROPIC, "shared-name-20260101") is anthropic_spec
    assert known.find(LlmProvider.OPENAI, "shared-name-20260101") is None


def test_two_embedding_models_and_no_choice_is_refused():
    """The defect this setting exists for.

    Picking the first declared made "which model embeds my index" a property of
    YAML ordering. Adding a second one above the first would silently re-embed
    new chunks against a different model - and if the two are the same width,
    nothing downstream notices: the dimension check passes and cosine distance
    between vectors from two models is a number, just a meaningless one. So an
    unstated choice is refused rather than guessed.
    """
    both = registry(
        spec("local:embed", ModelTier.SMALL, chat=False, embeddings=True),
        spec("hosted:embed", ModelTier.SMALL, chat=False, embeddings=True),
    )

    with pytest.raises(ModelNotConfigured) as raised:
        ModelRouter(both).embedding_model()

    assert set(raised.value.context["embedding_models"]) == {"local:embed", "hosted:embed"}


def test_the_configured_embedding_model_is_the_one_used():
    both = registry(
        spec("local:embed", ModelTier.SMALL, chat=False, embeddings=True),
        spec("hosted:embed", ModelTier.SMALL, chat=False, embeddings=True),
    )

    router = ModelRouter(both, embedding_model_key="hosted:embed")

    # Not the first declared, which is the whole point.
    assert router.embedding_model().key == "hosted:embed"


def test_an_embedding_model_that_is_not_declared_says_so():
    router = ModelRouter(
        registry(spec("embed", ModelTier.SMALL, chat=False, embeddings=True)),
        embedding_model_key="openai:embed",
    )

    with pytest.raises(ModelNotConfigured) as raised:
        router.embedding_model()

    assert "not declared" in str(raised.value)
    assert raised.value.context["requested"] == "openai:embed"


def test_naming_a_chat_model_for_embeddings_is_a_different_message():
    """A declared key that cannot embed is a different mistake from a typo, and
    the operator can only fix the one they actually made."""
    router = ModelRouter(
        registry(
            spec("chat", ModelTier.SMALL),
            spec("embed", ModelTier.SMALL, chat=False, embeddings=True),
        ),
        embedding_model_key="chat",
    )

    with pytest.raises(ModelNotConfigured) as raised:
        router.embedding_model()

    assert "does not support embeddings" in str(raised.value)
    assert raised.value.context["embedding_models"] == ["embed"]
