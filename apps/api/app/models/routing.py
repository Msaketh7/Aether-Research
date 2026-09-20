"""Role and mode to model, with declared fallbacks (ADR 0007).

A deep research run makes dozens of calls of wildly different difficulty.
Splitting a question into subtasks is easy; judging whether a quote actually
supports a claim is hard. Sending everything to the strongest model spends the
run's budget on trivia; sending everything to the cheapest degrades exactly the
judgements the product is sold on.

So the choice is a **policy**, expressed as data here and measurable by the
evaluation suite in Phase 18, rather than a model name typed into an agent.

## The policy

Base tier per role, from the build plan and ADR 0007:

| Role                | Tier      | Why                                             |
| ------------------- | --------- | ----------------------------------------------- |
| planner             | small     | decomposition is structured and easy            |
| claim_normalizer    | small     | mechanical rewriting                            |
| researcher          | medium    | reading and summarising sources                 |
| evidence_extractor  | medium    | claim extraction (TDD 6.2)                      |
| citation_validator  | medium    | span matching with judgement at the edges       |
| verifier            | strong    | does this evidence actually support this claim  |
| critic              | strong    | is the coverage good enough to stop             |
| synthesizer         | strongest | the artifact the user actually reads            |

Research mode then shifts that: `deep` uses the base tier, `quick` and
`conversational` step down one - they are the modes that trade depth for
latency and cost, and the tier is where that trade is actually made.

Two floors keep the shift from doing damage: the synthesizer never drops below
`strong`, because a cheap final synthesis is the one degradation a reader sees
directly; and no role drops below `small`, which is the bottom of the ladder.

## Fallbacks

A resolution is a *chain*, not a model: the preferred model, then the other
models in that tier (declaration order, which is how an operator states a
provider preference), then the next tier down. The gateway walks it when a model
fails in a way that a different model could survive. The chain is capped, because
an unbounded failover walk is just a slower outage that costs more.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.core.enums import AgentName, ResearchMode
from app.core.logging import get_logger
from app.models.errors import ModelNotConfigured
from app.models.registry import ModelRegistry, ModelSpec, ModelTier

logger = get_logger(__name__)

#: Base tier per agent role. See the table in the module docstring.
DEFAULT_ROLE_TIERS: dict[AgentName, ModelTier] = {
    AgentName.PLANNER: ModelTier.SMALL,
    AgentName.CLAIM_NORMALIZER: ModelTier.SMALL,
    AgentName.RESEARCHER: ModelTier.MEDIUM,
    AgentName.EVIDENCE_EXTRACTOR: ModelTier.MEDIUM,
    AgentName.CITATION_VALIDATOR: ModelTier.MEDIUM,
    AgentName.VERIFIER: ModelTier.STRONG,
    AgentName.CRITIC: ModelTier.STRONG,
    AgentName.SYNTHESIZER: ModelTier.STRONGEST,
}

#: How many tiers each mode moves the base. Only downward: a mode must not be
#: able to spend more than the role's declared ceiling.
MODE_TIER_SHIFT: dict[ResearchMode, int] = {
    ResearchMode.DEEP: 0,
    ResearchMode.QUICK: -1,
    ResearchMode.CONVERSATIONAL: -1,
}

#: The one role with a floor above the bottom of the ladder.
ROLE_TIER_FLOORS: dict[AgentName, ModelTier] = {
    AgentName.SYNTHESIZER: ModelTier.STRONG,
}

#: Ceiling on the failover chain. Four attempts across at most two tiers is
#: enough to survive one provider being down; beyond that the run should fail
#: and say so rather than spend the budget discovering the same outage.
MAX_FALLBACKS = 3


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """The model to use and what to try if it fails."""

    role: AgentName
    mode: ResearchMode
    tier: ModelTier
    primary: ModelSpec
    fallbacks: Sequence[ModelSpec]

    @property
    def chain(self) -> tuple[ModelSpec, ...]:
        """Every model to attempt, in order."""
        return (self.primary, *self.fallbacks)


class ModelRouter:
    """Turns (role, mode) into a model chain, using only declared models."""

    def __init__(
        self,
        registry: ModelRegistry,
        *,
        role_tiers: dict[AgentName, ModelTier] | None = None,
        mode_shift: dict[ResearchMode, int] | None = None,
        max_fallbacks: int = MAX_FALLBACKS,
        embedding_model_key: str | None = None,
    ) -> None:
        self._registry = registry
        self._role_tiers = role_tiers or dict(DEFAULT_ROLE_TIERS)
        self._mode_shift = mode_shift or dict(MODE_TIER_SHIFT)
        self._max_fallbacks = max_fallbacks
        self._embedding_model_key = embedding_model_key

    def tier_for(self, role: AgentName, mode: ResearchMode) -> ModelTier:
        """The tier this role gets in this mode, after the shift and the floor."""
        base = self._role_tiers.get(role, ModelTier.MEDIUM)
        shift = self._mode_shift.get(mode, 0)

        tier = base
        for _ in range(abs(shift)):
            tier = tier.step_down()

        floor = ROLE_TIER_FLOORS.get(role)
        if floor is not None and tier.rank < floor.rank:
            return floor
        return tier

    def resolve(self, *, role: AgentName, mode: ResearchMode) -> RoutingDecision:
        """Pick a model chain, or say precisely why there is none."""
        tier = self.tier_for(role, mode)
        candidates = self._candidates(tier)

        if not candidates:
            raise ModelNotConfigured(
                "No model is declared for this role.",
                context={
                    "role": role.value,
                    "mode": mode.value,
                    "tier": tier.value,
                    "declared": [spec.key for spec in self._registry],
                },
            )

        primary, *rest = candidates
        return RoutingDecision(
            role=role,
            mode=mode,
            tier=tier,
            primary=primary,
            fallbacks=tuple(rest[: self._max_fallbacks]),
        )

    def _candidates(self, tier: ModelTier) -> list[ModelSpec]:
        """Models to try, best first.

        The requested tier, then progressively cheaper ones. If the requested
        tier is empty, resolve *upward* first - that is what "route the
        synthesizer to the strongest configured tier" means when no model is
        declared at `strongest`. Resolving downward instead would silently give
        the final report the cheapest model in the registry.
        """
        exact = self._registry.by_tier(tier, chat_only=True)
        if not exact:
            upward = self._strongest_declared_at_or_above(tier)
            if upward is not None:
                exact = self._registry.by_tier(upward, chat_only=True)

        candidates = list(exact)
        seen = {spec.key for spec in candidates}

        current = candidates[0].tier if candidates else tier
        while current.rank > 0 and len(candidates) <= self._max_fallbacks:
            current = current.step_down()
            for spec in self._registry.by_tier(current, chat_only=True):
                if spec.key not in seen:
                    candidates.append(spec)
                    seen.add(spec.key)

        return candidates

    def _strongest_declared_at_or_above(self, tier: ModelTier) -> ModelTier | None:
        """The best tier that actually has models in it, at or below the ask.

        Searching downward from the requested tier and taking the first
        non-empty one gives "the strongest configured" without needing the
        operator to keep a `strongest` entry populated.
        """
        for candidate in sorted(ModelTier, key=lambda t: t.rank, reverse=True):
            if candidate.rank <= tier.rank and self._registry.by_tier(candidate, chat_only=True):
                return candidate
        return None

    def embedding_model(self) -> ModelSpec:
        """The model used for embeddings, pinned by configuration.

        One choice, not a per-role one, and deliberately not a fallback chain:
        an index whose vectors came from two models is not searchable, and it
        fails *silently* - the cosine distance between vectors from different
        models is a number, just a meaningless one. So there is no failing over
        here. Either the declared model answers or ingestion stops.

        Unset, this resolves to the single declared embedding model, and refuses
        a registry that declares more than one. Picking the first of several -
        which is what this did until the choice became configuration - makes
        "which model embeds my index" a property of YAML ordering, and quietly
        re-embeds against a different model when someone adds one above it.
        """
        models = self._registry.embedding_models()
        if not models:
            raise ModelNotConfigured(
                "No embedding model is declared in the registry.",
                context={"declared": [spec.key for spec in self._registry]},
            )

        declared = [spec.key for spec in models]

        if self._embedding_model_key is None:
            if len(models) > 1:
                raise ModelNotConfigured(
                    "Several embedding models are declared and none is chosen. "
                    "Set EMBEDDING_MODEL to the one that embeds this index.",
                    context={"embedding_models": declared},
                )
            return models[0]

        for spec in models:
            if spec.key == self._embedding_model_key:
                return spec

        # Named but unusable. Separating the two cases matters: a key that is
        # declared-but-not-an-embedding-model is a different mistake from a
        # typo, and the operator can only fix the one they made.
        if self._embedding_model_key in self._registry.specs:
            raise ModelNotConfigured(
                f"EMBEDDING_MODEL names {self._embedding_model_key!r}, which is declared "
                "but does not support embeddings.",
                context={"requested": self._embedding_model_key, "embedding_models": declared},
            )
        raise ModelNotConfigured(
            f"EMBEDDING_MODEL names {self._embedding_model_key!r}, which is not declared.",
            context={"requested": self._embedding_model_key, "embedding_models": declared},
        )
