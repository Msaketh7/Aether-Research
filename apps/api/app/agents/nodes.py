"""The contracts the research graph's nodes implement.

Phase 9 builds the graph; Phase 10 writes the agents. What joins them is here: a
Protocol per node, typed input, and a typed result with the node's measured
usage attached. An agent never returns prose for another agent to parse, and the
graph never has to guess what a node did.

No implementation lives in ``app/`` yet, and none is faked. A graph for a real
run needs every node, which is Phase 10, and the worker that runs one is Phase
13. The graph's tests drive it with scripted nodes, which are test doubles and
live under ``tests/``.

Two expectations every implementation must meet, because the graph relies on
them and cannot check them all:

* **Report usage honestly.** The run's cost and search ceilings are enforced
  from what nodes report. A node that raises loses its usage from the running
  total; the model-call ledger (Phase 16) is the authoritative record.
* **Never put retrieved content where a model reads instructions** (ADR 0011).
  State carries references and verbatim spans, not prompts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.agents.schemas import (
    CitationCheck,
    ClaimItem,
    ContradictionItem,
    Critique,
    EvidenceItem,
    NodeUsage,
    Plan,
    ReportDraft,
    SubtaskAssignment,
    TaskOutcome,
)
from app.agents.state import ResearchState


@dataclass(frozen=True, slots=True)
class NodeResult[T]:
    """What a node returns: its value, and what producing it cost."""

    value: T
    usage: NodeUsage = field(default_factory=NodeUsage)


class Planner(Protocol):
    async def plan(self, state: ResearchState) -> NodeResult[Plan]:
        """The next round's plan, for iteration ``state["iteration"] + 1``."""
        ...


class Researcher(Protocol):
    async def research(self, assignment: SubtaskAssignment) -> NodeResult[TaskOutcome]:
        """One subtask, within its allowances."""
        ...


class EvidenceExtractor(Protocol):
    async def extract(self, state: ResearchState) -> NodeResult[tuple[EvidenceItem, ...]]: ...


class ClaimNormalizer(Protocol):
    async def normalize(self, state: ResearchState) -> NodeResult[tuple[ClaimItem, ...]]: ...


class Verifier(Protocol):
    async def verify(self, state: ResearchState) -> NodeResult[tuple[ClaimItem, ...]]:
        """The claims it re-scored. They replace the earlier versions by id."""
        ...


class ContradictionChecker(Protocol):
    async def find_contradictions(
        self, state: ResearchState
    ) -> NodeResult[tuple[ContradictionItem, ...]]: ...


class Critic(Protocol):
    async def critique(self, state: ResearchState) -> NodeResult[Critique]:
        """The verdict on round ``state["iteration"]``."""
        ...


class Synthesizer(Protocol):
    async def synthesize(self, state: ResearchState) -> NodeResult[ReportDraft]: ...


class CitationValidator(Protocol):
    async def validate(self, state: ResearchState) -> NodeResult[CitationCheck]:
        """The verdict on ``state["report"]``, carrying its revision."""
        ...


@dataclass(frozen=True, slots=True)
class ResearchNodes:
    """Every node a research graph needs. A quick run uses a subset."""

    planner: Planner
    researcher: Researcher
    evidence_extractor: EvidenceExtractor
    claim_normalizer: ClaimNormalizer
    verifier: Verifier
    contradiction_checker: ContradictionChecker
    critic: Critic
    synthesizer: Synthesizer
    citation_validator: CitationValidator
