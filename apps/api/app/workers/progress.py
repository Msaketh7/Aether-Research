"""Where a run has got to, in the two fields a person actually reads.

``status`` is the phase the run has reached, and ``progress`` is the bar under
it. Both are derived from the node the graph has just finished, which means they
lag reality by at most one node - the graph does not know which node comes next
until it routes, and guessing would be worse than lagging.

**The bar is an estimate, and its shape says so.** A research run's length is
not known in advance: the critic decides whether there is another round, so a
run may do one round or its whole allowance. So discovery occupies a band, and a
round's position inside that band is its share of the *iteration ceiling*. A run
that satisfies its critic on the first of four rounds therefore jumps from about
a fifth of the band straight to synthesis. That jump is the honest rendering:
the run really did finish discovery early. What the numbers never do is go
backwards, which is the one thing a progress bar must not do, and a test walks a
four-round trace to prove it.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.agents.schemas import GraphNode
from app.core.enums import RunStatus

#: Which phase of the run each node belongs to. Evidence extraction and claim
#: normalization are part of researching: they read what the researchers found.
#: The critic is part of verifying - it is judging what has been verified, and a
#: status of its own would mean nothing to a reader.
NODE_STATUS: dict[GraphNode, RunStatus] = {
    GraphNode.PLANNER: RunStatus.PLANNING,
    GraphNode.RESEARCHER: RunStatus.RESEARCHING,
    GraphNode.EVIDENCE_EXTRACTOR: RunStatus.RESEARCHING,
    GraphNode.CLAIM_NORMALIZER: RunStatus.RESEARCHING,
    GraphNode.VERIFIER: RunStatus.VERIFYING,
    GraphNode.CONTRADICTION_CHECKER: RunStatus.VERIFYING,
    GraphNode.CRITIC: RunStatus.VERIFYING,
    # The answer is written, so it is synthesis as far as a reader's header is
    # concerned. A status of its own would mean adding a value to a vocabulary
    # the database, the API and the frontend all constrain, to name a phase that
    # lasts a few seconds and already has an event stream of its own.
    GraphNode.ANSWERER: RunStatus.SYNTHESIZING,
    GraphNode.SYNTHESIZER: RunStatus.SYNTHESIZING,
    GraphNode.CITATION_VALIDATOR: RunStatus.VALIDATING,
}

#: How far through one discovery round each node is. Rough by nature - these are
#: not measured durations - but ordered, which is the property that matters.
_ROUND_OFFSET: dict[GraphNode, float] = {
    GraphNode.PLANNER: 0.0,
    GraphNode.RESEARCHER: 0.35,
    GraphNode.EVIDENCE_EXTRACTOR: 0.55,
    GraphNode.CLAIM_NORMALIZER: 0.65,
    GraphNode.VERIFIER: 0.80,
    GraphNode.CONTRADICTION_CHECKER: 0.88,
    GraphNode.CRITIC: 0.95,
}

#: The band discovery occupies. It starts above zero because a claimed run has
#: already done something, and stops below synthesis because the report is not
#: written yet however many rounds ran.
DISCOVERY_FLOOR = 0.05
DISCOVERY_CEILING = 0.70

_WRITING_PROGRESS: dict[GraphNode, float] = {
    GraphNode.ANSWERER: 0.74,
    GraphNode.SYNTHESIZER: 0.85,
    GraphNode.CITATION_VALIDATOR: 0.92,
}

#: The order the graph visits nodes in, for picking the furthest-advanced of a
#: superstep. Only the researcher ever fans out, so a superstep almost always
#: holds one name; this is what keeps "almost" from being an assumption.
_ORDER: tuple[GraphNode, ...] = (
    GraphNode.PLANNER,
    GraphNode.RESEARCHER,
    GraphNode.EVIDENCE_EXTRACTOR,
    GraphNode.CLAIM_NORMALIZER,
    GraphNode.VERIFIER,
    GraphNode.CONTRADICTION_CHECKER,
    GraphNode.CRITIC,
    GraphNode.ANSWERER,
    GraphNode.SYNTHESIZER,
    GraphNode.CITATION_VALIDATOR,
)


def furthest(nodes: tuple[GraphNode, ...]) -> GraphNode:
    """The node a superstep got furthest with. Raises on an empty superstep."""
    return max(nodes, key=_ORDER.index)


def in_order(nodes: Iterable[GraphNode]) -> tuple[GraphNode, ...]:
    """The distinct nodes of a superstep, in the order the graph visits them.

    The row only needs the furthest one; the progress *stream* needs each of
    them, and in an order a reader can follow - a fan-out reports one node
    several times, and two different nodes in one superstep must not be
    announced back to front.
    """
    return tuple(sorted(set(nodes), key=_ORDER.index))


def status_for(node: GraphNode) -> RunStatus:
    return NODE_STATUS[node]


def progress_for(node: GraphNode, *, iteration: int, max_iterations: int) -> float:
    """The bar after ``node`` finished on round ``iteration``.

    ``iteration`` is the graph's round counter, which is 1 once the first plan
    exists. A round beyond the ceiling - which the governor does not allow, but
    which a configuration change under a resumed run could produce - is clamped
    to the top of the discovery band rather than pushed past synthesis.
    """
    writing = _WRITING_PROGRESS.get(node)
    if writing is not None:
        return writing

    rounds = max(1, max_iterations)
    # Round 1 starts at the floor of the band, not at its share of it: a run
    # that has planned has visibly started.
    position = (max(0, iteration - 1) + _ROUND_OFFSET[node]) / rounds
    span = DISCOVERY_CEILING - DISCOVERY_FLOOR
    return round(min(DISCOVERY_CEILING, DISCOVERY_FLOOR + span * position), 2)
