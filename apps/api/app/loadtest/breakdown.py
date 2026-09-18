"""Where a run's time went, read back from the ledger (Phase 22).

The load test says how long a run took. This says what it was doing, and it
does not need an instrument: Phase 16 already writes an ``agent_runs`` row per
node execution with its own ``latency_ms``, and Phase 17 does the same for
every model and tool call underneath. So profiling this system starts as a
``GROUP BY``, not as a sampler.

**That is the right altitude to start at.** A CPU profile of an asyncio worker
tells you which Python functions burned cycles, which is the answer to a
question nobody asked yet. "Extraction is forty percent of a run" is the
question - and it is answerable exactly, over a hundred real runs, from rows
the system wrote while doing its job.

**Sum, not mean, decides what to optimise.** A node that takes 900 ms and runs
once is a worse target than one that takes 200 ms and runs eight times, and a
table sorted by mean says the opposite. Both columns are here; the ranking is
by total.

**The rows say *agent*, not *node*, and two nodes share one agent.**
``NODE_AGENT`` in ``app.observability.ledger`` maps both the contradiction
checker and the critic to ``AgentName.CRITIC``, because the product's
vocabulary has nine agents and those are one of them. So "critic: 50
executions" over 25 runs is two nodes running once each, not a loop that ran
twice - a reading this file got wrong once before the mapping was checked.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.loadtest.measure import Latency, summarise


@dataclass(frozen=True, slots=True)
class NodeCost:
    """One agent's share of the work, over every run in a profile."""

    agent: str
    executions: int
    #: ``None`` when no execution of this node recorded a latency - which is
    #: *not measured*, and is what a node that failed before its span closed
    #: leaves behind.
    latency: Latency | None
    total_seconds: float

    def as_dict(self) -> dict[str, object]:
        return {
            "agent": self.agent,
            "executions": self.executions,
            "total_seconds": round(self.total_seconds, 3),
            "latency_seconds": None if self.latency is None else self.latency.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class Breakdown:
    """Every node that ran, ranked by how much of the wall clock it holds."""

    nodes: tuple[NodeCost, ...]
    #: Seconds of node execution across every run measured. Larger than the
    #: profile's wall clock, and should be: four runs execute at once, and a
    #: run's researchers run in parallel inside it.
    total_seconds: float
    runs: int

    @property
    def per_run_seconds(self) -> float | None:
        return self.total_seconds / self.runs if self.runs else None

    def share_of(self, agent: str) -> float | None:
        """This node's fraction of all node time, or ``None`` if nothing ran."""
        if not self.total_seconds:
            return None
        for node in self.nodes:
            if node.agent == agent:
                return node.total_seconds / self.total_seconds
        return None

    def as_dict(self) -> dict[str, object]:
        return {
            "runs": self.runs,
            "total_node_seconds": round(self.total_seconds, 3),
            "per_run_node_seconds": (
                None if self.per_run_seconds is None else round(self.per_run_seconds, 3)
            ),
            "nodes": [node.as_dict() for node in self.nodes],
        }


def breakdown_of(rows: Sequence[tuple[str, int | None]], *, runs: int) -> Breakdown:
    """Fold ``(agent_name, latency_ms)`` rows into a ranked breakdown.

    A row with no latency still counts as an execution. Dropping it would make
    a node that keeps dying look cheap, which is the opposite of the truth.
    """
    seconds: dict[str, list[float]] = {}
    executions: dict[str, int] = {}
    for agent, latency_ms in rows:
        executions[agent] = executions.get(agent, 0) + 1
        if latency_ms is not None:
            seconds.setdefault(agent, []).append(latency_ms / 1000.0)

    nodes = [
        NodeCost(
            agent=agent,
            executions=count,
            latency=summarise(seconds.get(agent, [])),
            total_seconds=sum(seconds.get(agent, [])),
        )
        for agent, count in executions.items()
    ]
    nodes.sort(key=lambda node: node.total_seconds, reverse=True)
    return Breakdown(
        nodes=tuple(nodes),
        total_seconds=sum(node.total_seconds for node in nodes),
        runs=runs,
    )


def render(breakdown: Breakdown) -> list[str]:
    """The breakdown as Markdown table lines."""
    if not breakdown.nodes:
        return ["No node execution was recorded.", ""]
    header = "| agent | executions | total (s) | share | mean (s) | P95 (s) |"
    divider = "| --- | --- | --- | --- | --- | --- |"
    rows = []
    for node in breakdown.nodes:
        share = breakdown.share_of(node.agent)
        latency = node.latency
        rows.append(
            f"| {node.agent} | {node.executions} | {node.total_seconds:.1f} | "
            f"{'-' if share is None else f'{share * 100:.1f}%'} | "
            f"{'-' if latency is None else f'{latency.mean:.3f}'} | "
            f"{'-' if latency is None else f'{latency.p95:.3f}'} |"
        )
    return [header, divider, *rows, ""]
