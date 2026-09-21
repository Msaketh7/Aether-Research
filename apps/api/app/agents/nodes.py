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

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.agents.schemas import (
    AnswerDraft,
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


#: What a streaming agent hands each piece of its output to as it arrives.
#: Awaitable because every real destination is I/O - the run's event bus, in the
#: one case there is.
type DeltaSink = Callable[[str], Awaitable[None]]


async def discard(_text: str) -> None:
    """A sink that goes nowhere. What an answer written with nobody watching uses."""


class AnswerStream(Protocol):
    """Where a run's answer goes while it is being written.

    Per invocation, not per run and not per process: it belongs to the worker
    executing *this* attempt, which is why it travels in ``GraphContext``
    alongside ``resumed_at`` rather than in the checkpointed state. A resumed
    run gets a new one, pointed at the same client.

    ``finish`` is not optional, and it is not a close. An implementation is free
    to hold text back - the one that exists batches it, because every piece it
    publishes is a stored row - and without a final call there is no moment at
    which the last, short piece is known to be the last. The node calls it on
    the way out of the answerer whether the agent returned an answer, returned
    nothing, or raised: what has already been written belongs to the reader
    either way.
    """

    async def write(self, text: str) -> None: ...

    async def finish(self) -> None: ...


class DiscardedAnswerStream:
    """The default: an answer nobody is watching is still written and stored."""

    async def write(self, text: str) -> None:
        return None

    async def finish(self) -> None:
        return None


class Answerer(Protocol):
    async def answer(
        self, state: ResearchState, *, on_delta: DeltaSink
    ) -> NodeResult[AnswerDraft | None]:
        """The direct answer, or ``None`` when there was nothing to answer from.

        Pieces are handed to ``on_delta`` as they arrive. Whatever it does with
        them is not this node's business and must not be able to stop it: the
        answer's record is the value returned, not the stream.
        """
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
    answerer: Answerer
    synthesizer: Synthesizer
    citation_validator: CitationValidator
