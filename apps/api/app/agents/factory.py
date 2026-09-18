"""Assembling the nine agents into a ``ResearchNodes`` the graph can run.

One place where the wiring lives, so that a question like "which agents can
reach the network" has one answer to read rather than nine. What it says, in the
order the arguments say it:

* Every model call goes through one gateway, so the run's calls share one
  concurrency semaphore and one ledger.
* Every network call goes through one toolbelt, so they share one SSRF-guarded
  client and one connection pool. The synthesizer and the citation validator are
  not given one at all - not a restricted one, not an empty one. An agent that
  can be told what to write must not also be able to fetch what it is told to
  fetch (TDD 15.3), and the strongest form of that is having no way to.
* The researchers share one collector, so the fetch-and-ingest bound is one
  number rather than one per channel.

The caller owns the toolbelt's lifetime: it holds an HTTP client, and the worker
that runs graphs (Phase 13) is what knows when the process is finished with it.
``build_research_nodes`` therefore accepts one as readily as it builds one.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agents.citations import CitationValidator
from app.agents.critic import CriticAgent
from app.agents.extraction import ClaimNormalizerAgent, EvidenceAgent
from app.agents.nodes import ResearchNodes
from app.agents.planner import PlannerAgent
from app.agents.researchers.collect import SourceCollector
from app.agents.researchers.data import DataResearchAgent
from app.agents.researchers.documents import DocumentResearchAgent
from app.agents.researchers.router import ResearchRouter
from app.agents.researchers.web import WebResearchAgent
from app.agents.synthesis import SynthesisAgent
from app.agents.verification import ContradictionAgent, VerificationAgent
from app.cache import ResponseCache
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.session import Database
from app.models.gateway import LLMGateway
from app.observability.metrics import Metrics
from app.observability.retrieval import MeasuredRetriever
from app.retrieval.factory import build_document_ingestor, build_retriever
from app.retrieval.ingestion import DocumentIngestor
from app.retrieval.retriever import Retriever
from app.sources.base import CallRecorder
from app.sources.toolbelt import Toolbelt, build_toolbelt
from app.storage import ObjectStorage

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ResearchDependencies:
    """What the agents are built on. Assembled once per worker process."""

    gateway: LLMGateway
    database: Database
    storage: ObjectStorage
    toolbelt: Toolbelt
    ingestor: DocumentIngestor
    retriever: Retriever

    async def close(self) -> None:
        """Release what this owns. The gateway and database outlive it."""
        await self.toolbelt.close()


def build_dependencies(
    settings: Settings,
    *,
    gateway: LLMGateway,
    database: Database,
    storage: ObjectStorage,
    cache: ResponseCache | None = None,
    tool_recorder: CallRecorder | None = None,
    metrics: Metrics | None = None,
) -> ResearchDependencies:
    """Build the shared machinery once, from configuration.

    The gateway is passed to ingestion and retrieval as well as to the agents,
    so the vectors a researcher writes and the vectors an extractor searches are
    produced by the same embedding model. Different models there is not an error
    anywhere - it just makes retrieval quietly worse (ADR 0012).

    The cache is the process's, for the same reason the gateway is: one shared
    by every researcher in a run is what makes two of them fetching one page
    fetch it once (Phase 15). One per agent would be one per nobody.

    ``tool_recorder`` is where every tool call is written. Left out, the belt
    logs them; the worker passes one that also writes `tool_calls`, hung from
    whichever node is running (Phase 16).

    ``metrics`` wraps the retriever so retrieval latency and result counts are
    graphed (Phase 17). Left out, retrieval behaves identically and reports
    nothing, which is what a test wants.
    """
    return ResearchDependencies(
        gateway=gateway,
        database=database,
        storage=storage,
        toolbelt=build_toolbelt(settings, cache=cache, recorder=tool_recorder),
        ingestor=build_document_ingestor(
            settings, database=database, storage=storage, gateway=gateway
        ),
        retriever=_measured(build_retriever(settings, database=database, gateway=gateway), metrics),
    )


def _measured(retriever: Retriever, metrics: Metrics | None) -> Retriever:
    return retriever if metrics is None else MeasuredRetriever(retriever, metrics)


def build_research_nodes(
    settings: Settings, *, dependencies: ResearchDependencies
) -> ResearchNodes:
    """The nine nodes the graph runs, wired from configuration."""
    gateway = dependencies.gateway
    collector = SourceCollector(
        toolbelt=dependencies.toolbelt,
        ingestor=dependencies.ingestor,
        concurrency=settings.researcher_fetch_concurrency,
    )
    router = ResearchRouter(
        web=WebResearchAgent(
            gateway,
            toolbelt=dependencies.toolbelt,
            collector=collector,
            results_per_query=settings.researcher_results_per_query,
        ),
        documents=DocumentResearchAgent(
            gateway,
            retriever=dependencies.retriever,
            database=dependencies.database,
        ),
        data=DataResearchAgent(gateway, toolbelt=dependencies.toolbelt, collector=collector),
    )

    logger.info(
        "research agents assembled",
        extra={
            "channels": sorted(channel.value for channel in router.channels),
            "tools": sorted(tool.value for tool in dependencies.toolbelt.permitted),
            "dispatch_width": settings.max_subtasks_per_iteration,
        },
    )
    return ResearchNodes(
        planner=PlannerAgent(gateway, dispatch_width=settings.max_subtasks_per_iteration),
        researcher=router,
        evidence_extractor=EvidenceAgent(
            gateway,
            retriever=dependencies.retriever,
            passages_per_call=settings.evidence_passages_per_call,
            max_calls=settings.evidence_max_calls_per_round,
        ),
        claim_normalizer=ClaimNormalizerAgent(gateway),
        verifier=VerificationAgent(gateway),
        contradiction_checker=ContradictionAgent(gateway),
        critic=CriticAgent(gateway),
        synthesizer=SynthesisAgent(gateway),
        citation_validator=CitationValidator(),
    )
