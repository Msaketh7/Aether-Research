"""The model registry: which models exist, what they cost, what they can do.

Models are **data**, loaded from ``registry.yaml`` (TDD 6.1). That is deliberate:
prices change, a provider ships a new tier, an operator wants a different mix -
none of which should be a code change, and all of which need to be reviewable in
a diff that a non-engineer can read.

## Prices are dated, sourced, and allowed to be unknown

Every price carries the date it was taken and where from. Entries the repository
cannot verify ship **unpriced** (`null`), and `cost_usd` then returns `None`
rather than `0.0`.

That distinction is the project rule about not confusing *not measured* with
*zero*, and it has teeth here: Phase 16 enforces a per-run spend ceiling. A model
whose price is unknown must be visibly un-costable, so the budget code can refuse
it or flag it - not quietly treat every call to it as free and let a run overrun
its ceiling while the ledger reads $0.00.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from app.core.enums import LlmProvider
from app.core.logging import get_logger
from app.models.base import EmbeddingPurpose, TokenUsage
from app.models.errors import ModelNotConfigured

logger = get_logger(__name__)

DEFAULT_REGISTRY_PATH = Path(__file__).with_name("registry.yaml")


class ModelTier(StrEnum):
    """How capable - and how expensive - a model is (TDD 6.2).

    Declaration order is the ordering: ``SMALL < MEDIUM < STRONG < STRONGEST``.
    Tiers are relative *within this system*, not a vendor's marketing name, so a
    new model is placed by what it is used for rather than what it is called.
    """

    SMALL = "small"
    MEDIUM = "medium"
    STRONG = "strong"
    STRONGEST = "strongest"

    @property
    def rank(self) -> int:
        return _TIER_ORDER.index(self)

    def step_down(self) -> ModelTier:
        """The next cheaper tier, or itself at the floor."""
        return _TIER_ORDER[max(0, self.rank - 1)]


_TIER_ORDER: tuple[ModelTier, ...] = (
    ModelTier.SMALL,
    ModelTier.MEDIUM,
    ModelTier.STRONG,
    ModelTier.STRONGEST,
)


@dataclass(frozen=True, slots=True)
class Pricing:
    """What a model costs, and how we know.

    ``as_of`` and ``source`` are not documentation for its own sake: a cost
    report is only trustworthy if the prices behind it can be dated.
    """

    input_usd_per_mtok: float
    output_usd_per_mtok: float
    as_of: dt.date
    source: str


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One declared model."""

    key: str
    provider: LlmProvider
    model_id: str
    tier: ModelTier
    context_window: int
    max_output_tokens: int
    #: ``None`` means the price is not known here - see the module docstring.
    pricing: Pricing | None = None

    # --- capabilities ---------------------------------------------------
    # Flags rather than assumptions. ADR 0007 anticipated this: the interface is
    # the lowest common denominator, and anything above it is declared.
    #: Whether this model can hold a conversation at all. An embeddings model
    #: cannot, and must never appear in a chat fallback chain - a failover that
    #: lands on one produces a confusing 400 instead of an answer.
    supports_chat: bool = True
    supports_streaming: bool = True
    supports_structured_output: bool = True
    #: Current Anthropic models reject `temperature` with a 400. Sending it
    #: anyway is a hard failure, so it has to be declared per model.
    supports_temperature: bool = True
    supports_token_counting: bool = False
    supports_embeddings: bool = False
    embedding_dimensions: int | None = None
    #: Task prefixes for an asymmetric embedding model, prepended by the gateway
    #: (see ``EmbeddingPurpose``). Empty for a symmetric model, which is why they
    #: default to empty rather than to a guess: a prefix a model was not trained
    #: with is noise added to every vector.
    embedding_document_prefix: str = ""
    embedding_query_prefix: str = ""

    def embedding_prefix(self, purpose: EmbeddingPurpose) -> str:
        return (
            self.embedding_document_prefix
            if purpose is EmbeddingPurpose.DOCUMENT
            else self.embedding_query_prefix
        )

    @property
    def is_priced(self) -> bool:
        return self.pricing is not None

    def cost_usd(self, usage: TokenUsage) -> float | None:
        """What a call cost, or ``None`` when the price is not known.

        Never 0.0 for an unpriced model. A free local model is priced *at* zero
        with a source saying so, which is a different fact.
        """
        if self.pricing is None:
            return None
        million = 1_000_000
        return round(
            usage.prompt_tokens / million * self.pricing.input_usd_per_mtok
            + usage.completion_tokens / million * self.pricing.output_usd_per_mtok,
            6,
        )


@dataclass
class ModelRegistry:
    """Every declared model, indexed for the router's questions."""

    specs: Mapping[str, ModelSpec] = field(default_factory=dict)

    def get(self, key: str) -> ModelSpec:
        spec = self.specs.get(key)
        if spec is None:
            raise ModelNotConfigured(
                "That model is not declared in the registry.",
                context={"key": key, "declared": sorted(self.specs)},
            )
        return spec

    def by_tier(
        self,
        tier: ModelTier,
        *,
        providers: Iterable[LlmProvider] | None = None,
        chat_only: bool = False,
    ) -> list[ModelSpec]:
        """Models in a tier, in declaration order.

        Declaration order is the preference order, so an operator expresses
        "prefer Anthropic for strong work" by putting it first in the YAML
        rather than by learning a scoring rule.

        ``chat_only`` is explicit rather than implied, because the router wants
        it on every call and an embeddings index wants it off.
        """
        allowed = set(providers) if providers is not None else None
        return [
            spec
            for spec in self.specs.values()
            if spec.tier is tier
            and (allowed is None or spec.provider in allowed)
            and (not chat_only or spec.supports_chat)
        ]

    def embedding_models(self) -> list[ModelSpec]:
        return [spec for spec in self.specs.values() if spec.supports_embeddings]

    def providers(self) -> set[LlmProvider]:
        return {spec.provider for spec in self.specs.values()}

    def unpriced(self) -> list[ModelSpec]:
        """Declared models whose cost cannot be computed.

        Surfaced at startup so an operator learns about it then, rather than
        from a run whose cost ledger reads zero.
        """
        return [spec for spec in self.specs.values() if not spec.is_priced]

    def __iter__(self) -> Iterator[ModelSpec]:
        return iter(self.specs.values())

    def __len__(self) -> int:
        return len(self.specs)


def load_registry(path: Path | None = None) -> ModelRegistry:
    """Read the declared models from YAML.

    Fails loudly on a malformed entry. A registry that silently drops a model it
    could not parse would show up as a routing failure at run time, a long way
    from the typo that caused it.
    """
    source = path or DEFAULT_REGISTRY_PATH
    raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    entries = raw.get("models") or []

    specs: dict[str, ModelSpec] = {}
    for entry in entries:
        spec = _parse_spec(entry, source)
        if spec.key in specs:
            raise ValueError(f"Duplicate model key {spec.key!r} in {source}.")
        specs[spec.key] = spec

    registry = ModelRegistry(specs=specs)
    if unpriced := registry.unpriced():
        logger.warning(
            "models declared without a price; their calls cannot be costed",
            extra={"models": [spec.key for spec in unpriced]},
        )
    return registry


def _parse_spec(entry: Mapping[str, Any], source: Path) -> ModelSpec:
    try:
        provider = LlmProvider(entry["provider"])
        tier = ModelTier(entry["tier"])
        pricing = _parse_pricing(entry.get("pricing"))
        return ModelSpec(
            key=str(entry["key"]),
            provider=provider,
            model_id=str(entry["model_id"]),
            tier=tier,
            context_window=int(entry["context_window"]),
            max_output_tokens=int(entry["max_output_tokens"]),
            pricing=pricing,
            supports_chat=bool(entry.get("supports_chat", True)),
            supports_streaming=bool(entry.get("supports_streaming", True)),
            supports_structured_output=bool(entry.get("supports_structured_output", True)),
            supports_temperature=bool(entry.get("supports_temperature", True)),
            supports_token_counting=bool(entry.get("supports_token_counting", False)),
            supports_embeddings=bool(entry.get("supports_embeddings", False)),
            embedding_dimensions=(
                int(entry["embedding_dimensions"])
                if entry.get("embedding_dimensions") is not None
                else None
            ),
            embedding_document_prefix=str(entry.get("embedding_document_prefix", "")),
            embedding_query_prefix=str(entry.get("embedding_query_prefix", "")),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid model entry in {source}: {entry!r} ({exc})") from exc


def _parse_pricing(raw: Mapping[str, Any] | None) -> Pricing | None:
    """``None`` and a null price both mean "not known here"."""
    if raw is None:
        return None
    if raw.get("input_usd_per_mtok") is None or raw.get("output_usd_per_mtok") is None:
        return None

    as_of = raw["as_of"]
    return Pricing(
        input_usd_per_mtok=float(raw["input_usd_per_mtok"]),
        output_usd_per_mtok=float(raw["output_usd_per_mtok"]),
        as_of=as_of if isinstance(as_of, dt.date) else dt.date.fromisoformat(str(as_of)),
        source=str(raw["source"]),
    )
