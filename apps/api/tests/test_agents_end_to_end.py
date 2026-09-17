"""A whole research run: the real graph, the real agents, a scripted model.

Every other file in this phase tests one agent against its own inputs. This one
runs the nine of them through the Phase 9 graph and asserts on what comes out
the far end - because the defects that matter most here are the ones between
agents. A claim id that moves between the normalizer and the verifier, a
catalogue that renumbers between the writer and the validator, a task key that
does not match the subtask it came from: each is invisible in a unit test of
either side and fatal to the product.

What is real: the graph, its governance, the checkpointer's serializer, the
gateway with its routing and pricing, and all nine agents. What is scripted: the
model's answers, and the tools - the toolbelt and the ingestion pipeline have
their own suites against real HTTP and real Postgres, and re-running them here
would test them twice and this not at all.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from app.agents.checkpoint import build_serializer
from app.agents.citations import CitationValidator
from app.agents.critic import CriticAgent
from app.agents.extraction import ClaimNormalizerAgent, EvidenceAgent
from app.agents.graph import GraphBounds
from app.agents.nodes import ResearchNodes
from app.agents.outputs import (
    ClaimsOutput,
    ClaimVerdict,
    CritiqueOutput,
    EvidenceCandidate,
    EvidenceOutput,
    MissingItem,
    PlanOutput,
    ProposedClaim,
    ProposedSubtask,
    ReportOutput,
    SearchQueries,
    SectionOutput,
    SourceChoice,
    SourceSelection,
    VerificationOutput,
)
from app.agents.planner import PlannerAgent
from app.agents.researchers.router import ResearchRouter
from app.agents.researchers.web import WebResearchAgent
from app.agents.runtime import ResearchGraphRunner
from app.agents.schemas import ResearchChannel, StopReason
from app.agents.state import RunBrief
from app.agents.synthesis import SynthesisAgent
from app.agents.verification import ContradictionAgent, VerificationAgent
from app.core.enums import (
    ClaimStatus,
    ClaimType,
    EvidenceStance,
    ReportSectionKind,
    ResearchMode,
    TaskPriority,
)
from tests.support import agents as fake
from tests.support.graph import RecordedRuns, ScriptedProbe
from tests.test_researchers import FakeCollector, FakeToolbelt, result

#: Two pages, so a second round can find something the first did not. A page
#: the run already has is not re-extracted from - it has been read.
PAGES = {
    "https://a.test/pricing": (
        "Provider A publishes list pricing for its inference fleet. "
        "Inference on H100 instances is priced at $4.10 per GPU-hour. "
        "Capacity is allocated quarterly and sold in one-year reservations."
    ),
    "https://b.test/pricing": (
        "A pricing review of the inference market. "
        "Provider A lists H100 capacity at $4.10 per GPU-hour as of March. "
        "Reservations remain the cheapest route."
    ),
}
QUOTES = {
    "https://a.test/pricing": "Inference on H100 instances is priced at $4.10 per GPU-hour.",
    "https://b.test/pricing": ("Provider A lists H100 capacity at $4.10 per GPU-hour as of March."),
}
PAGE = PAGES["https://a.test/pricing"]
QUOTE = QUOTES["https://a.test/pricing"]


# --- the script -------------------------------------------------------------------


def plan(*questions: str, iteration_channel: ResearchChannel = ResearchChannel.WEB):
    return PlanOutput(
        research_goal="Compare inference pricing across providers",
        subtasks=tuple(
            ProposedSubtask(
                question=question, priority=TaskPriority.HIGH, channel=iteration_channel
            )
            for question in questions
        ),
    )


def a_round(
    *,
    sufficient: bool = True,
    claim_key: str = "provider a | h100 price | 2026",
    pick: int = 1,
):
    """One round's answers, in the order the graph asks for them.

    There is no contradiction answer here, and that is the point: with one claim
    no two claims share a key, so the contradiction checker makes no model call
    at all. The node still runs - it has its own budget and cancel check - and
    costs nothing.
    """
    return [
        SearchQueries(queries=("h100 gpu-hour price 2026",)),
        SourceSelection(selected=(SourceChoice(result=pick),)),
        EvidenceOutput(
            evidence=(
                EvidenceCandidate(
                    passage=1,
                    quote=QUOTES[list(PAGES)[pick - 1]],
                    stance=EvidenceStance.SUPPORTS,
                ),
            )
        ),
        ClaimsOutput(
            claims=(
                ProposedClaim(
                    text="Provider A charges $4.10 per H100 GPU-hour.",
                    normalized_key=claim_key,
                    claim_type=ClaimType.QUANTITATIVE,
                    evidence=(1,),
                    confidence=0.7,
                ),
            )
        ),
        VerificationOutput(
            verdicts=(ClaimVerdict(claim=1, status=ClaimStatus.VERIFIED, confidence=0.9),)
        ),
        CritiqueOutput(
            sufficient=sufficient,
            missing=()
            if sufficient
            else (MissingItem(description="Pricing for provider B", subtask=1),),
            rationale="",
        ),
    ]


def a_report(content: str = "Provider A charges $4.10 per GPU-hour [1]."):
    return ReportOutput(
        title="Inference pricing",
        sections=(
            SectionOutput(
                kind=ReportSectionKind.EXECUTIVE_SUMMARY,
                heading="Summary",
                content_md=content,
            ),
        ),
    )


def build(*answers: Any, width: int = 4):
    """A runner over the real agents, answering from ``answers`` in order."""
    gateway, model, recorder = fake.gateway(*answers)
    toolbelt = FakeToolbelt(results=[result(url, title="Pricing") for url in PAGES])
    collector = FakeCollector()
    # The collector derives a source id from the URL; the retriever returns the
    # chunk belonging to that same source, or the evidence would point at a
    # source the run never gathered.
    retriever = fake.ScriptedRetriever(
        chunks=[
            fake.chunk(f"chunk-{url}", text, source_id=fake.ident(url), url=url)
            for url, text in PAGES.items()
        ]
    )

    nodes = ResearchNodes(
        planner=PlannerAgent(gateway, dispatch_width=width),
        researcher=ResearchRouter(
            web=WebResearchAgent(gateway, toolbelt=toolbelt, collector=collector)
        ),
        evidence_extractor=EvidenceAgent(gateway, retriever=retriever),
        claim_normalizer=ClaimNormalizerAgent(gateway),
        verifier=VerificationAgent(gateway),
        contradiction_checker=ContradictionAgent(gateway),
        critic=CriticAgent(gateway),
        synthesizer=SynthesisAgent(gateway),
        citation_validator=CitationValidator(),
    )
    probe = ScriptedProbe()
    runner = ResearchGraphRunner(
        nodes=nodes,
        checkpointer=InMemorySaver(serde=build_serializer()),
        probe=probe,
        bounds=GraphBounds(
            max_subtasks_per_iteration=width, max_concurrency=2, node_timeout_seconds=10.0
        ),
        recorder=RecordedRuns(),
    )
    return runner, model, recorder, probe, collector


def brief(mode: ResearchMode = ResearchMode.DEEP, **budget: object) -> RunBrief:
    return fake.brief(mode=mode, depth=3, budget=fake.budget(**budget))


# --- the run ----------------------------------------------------------------------


async def test_a_question_becomes_a_report_whose_every_citation_resolves():
    runner, _, recorder, _, collector = build(
        plan("What does provider A charge per GPU-hour?"),
        *a_round(),
        a_report(),
    )

    state = await runner.run(brief())

    assert state["stop"] is None
    assert state["contradictions"] == [], "one claim cannot disagree with itself"
    assert len(state["sources"]) == 1
    assert len(state["evidence"]) == 1
    assert len(state["claims"]) == 1
    assert state["claims"][0].status is ClaimStatus.VERIFIED

    check = state["citation_check"]
    assert check is not None and check.passed
    assert (check.checked, check.valid, check.rejected) == (1, 1, 0)
    assert state["report"] is not None
    assert state["report"].revision == 0
    assert collector.limits == [20], "its whole share of the source ceiling"
    assert len(recorder.calls) == 8, "one ledger row per model call, and the validator makes none"


async def test_the_chain_from_the_report_back_to_a_retrieved_source_holds():
    """Marker to claim to evidence to document to source, walked in a test
    rather than asserted by the validator that built it."""
    runner, _, _, _, _ = build(
        plan("What does provider A charge per GPU-hour?"), *a_round(), a_report()
    )

    state = await runner.run(brief())

    section = state["report"].sections[0]
    assert "[1]" in section.content_md
    claim = next(c for c in state["claims"] if c.id == section.claim_ids[0])
    evidence = next(e for e in state["evidence"] if e.id == claim.evidence_ids[0])
    source = next(s for s in state["sources"] if s.source_id == evidence.source_id)

    assert evidence.claim_text == QUOTE
    assert evidence.span_end - evidence.span_start == len(QUOTE)
    assert source.url == "https://a.test/pricing"


async def test_the_runs_cost_is_the_sum_of_what_its_agents_actually_spent():
    """Not an estimate. Eight calls at 180 tokens each, priced from the registry."""
    runner, model, recorder, _, _ = build(
        plan("What does provider A charge per GPU-hour?"), *a_round(), a_report()
    )

    state = await runner.run(brief())

    assert model.calls == 8
    assert state["token_usage"].total == 8 * 180
    assert state["estimated_cost"].measured
    assert state["estimated_cost"].usd == round(sum(c.cost_usd or 0 for c in recorder.calls), 6)
    assert state["search_queries"] == 1


async def test_an_unsatisfied_critic_buys_another_round_and_the_claim_accumulates():
    """The loop, and the reason claim identity is derived: the same assertion
    found twice is one claim with two rounds of evidence behind it, not two
    claims with half the support each."""
    runner, _, _, _, _ = build(
        plan("What does provider A charge per GPU-hour?"),
        *a_round(sufficient=False, pick=1),
        plan("Is that price corroborated anywhere else?"),
        *a_round(sufficient=True, pick=2),
        a_report(),
    )

    state = await runner.run(brief(max_iterations=2))

    assert state["iteration"] == 2
    assert len(state["sources"]) == 2, "a second page, found in the second round"
    assert len(state["evidence"]) == 2, "a span from each"
    assert len(state["claims"]) == 1, "one assertion, corroborated"
    assert len(state["claims"][0].evidence_ids) == 2, "both spans behind the one claim"
    assert state["critique"].sufficient


async def test_a_fabricated_citation_is_rejected_and_the_repair_fixes_it():
    """The repair loop end to end: the validator rejects, the graph sends the
    draft back with instructions, and the second draft passes."""
    runner, model, _, _, _ = build(
        plan("What does provider A charge per GPU-hour?"),
        *a_round(),
        a_report("Provider B charges $9.99 [7]."),
        a_report("Provider A charges $4.10 [1]."),
    )

    state = await runner.run(brief())

    assert state["citation_repairs"] == 1
    assert state["report"].revision == 1
    assert state["citation_check"].passed
    assert "This is a rewrite" in model.user_prompts()[-1]


async def test_a_run_that_can_never_cite_anything_does_not_pretend_otherwise():
    """Nothing was found, so there are no claims, so there is no report. The
    graph fails the run rather than returning uncited prose."""
    from app.agents.errors import SynthesisFailed

    runner, model, _, _, _ = build(
        plan("What does provider A charge per GPU-hour?"),
        SearchQueries(queries=("h100 gpu-hour price 2026",)),
        SourceSelection(selected=()),
        CritiqueOutput(sufficient=True),
    )

    try:
        await runner.run(brief(max_iterations=1))
    except SynthesisFailed:
        pass
    else:  # pragma: no cover - the assertion below explains the failure
        raise AssertionError("a run with no claims must not produce a report")

    # Extraction, normalization, verification and the contradiction check each
    # had nothing to work from, so none of them called a model: the planner, the
    # researcher's two calls and the critic are the whole run.
    assert model.calls == 4


async def test_a_quick_run_skips_the_critic_and_still_produces_a_cited_report():
    """PRD 5.1's trimmed graph, with evidence and claims kept: a citation
    resolves through them, so a quick report without them could cite nothing."""
    runner, model, _, _, _ = build(
        plan("What does provider A charge per GPU-hour?"),
        SearchQueries(queries=("h100 gpu-hour price 2026",)),
        SourceSelection(selected=(SourceChoice(result=1),)),
        EvidenceOutput(
            evidence=(EvidenceCandidate(passage=1, quote=QUOTE, stance=EvidenceStance.SUPPORTS),)
        ),
        ClaimsOutput(
            claims=(
                ProposedClaim(
                    text="Provider A charges $4.10 per H100 GPU-hour.",
                    normalized_key="provider a | h100 price | 2026",
                    claim_type=ClaimType.QUANTITATIVE,
                    evidence=(1,),
                    confidence=0.7,
                ),
            )
        ),
        a_report(),
    )

    state = await runner.run(brief(mode=ResearchMode.QUICK, max_iterations=1))

    assert state["critique"] is None
    assert state["citation_check"].passed
    assert model.calls == 6, "no verifier, no contradiction check, no critic"
    # Quick steps every role down a tier, with the synthesizer floored at strong.
    assert {request.model for request in model.prompts} == {
        "scripted-small",
        "scripted-strong",
    }


async def test_a_cancelled_run_stops_without_writing_a_report():
    runner, _, _, probe, _ = build(
        plan("What does provider A charge per GPU-hour?"), *a_round(), a_report()
    )
    probe.cancelled = True

    state = await runner.run(brief())

    assert state["stop"].reason is StopReason.CANCELLED
    assert state.get("report") is None


async def test_a_reached_limit_ends_discovery_and_the_report_carries_the_caveat():
    """FR-8: a limit is a normal outcome with a partial report, not a failure -
    and the caveat is built from measured values by the graph, not written by
    the synthesizer."""
    runner, _, _, _, _ = build(
        plan("What does provider A charge per GPU-hour?"),
        *a_round(sufficient=False),
        a_report(),
    )

    state = await runner.run(brief(max_iterations=1))

    assert state["stop"].reason is StopReason.ITERATIONS
    assert state["report"] is not None
    assert "Pricing for provider B" in state["report"].coverage_caveat
    assert state["citation_check"].passed


# --- assembly ------------------------------------------------------------------------


async def test_the_factory_wires_every_node_from_configuration(settings, database, artifact_store):
    """The wiring the worker will use (Phase 13), built once here.

    A factory is the easiest place for a phase to end with an agent that is
    never reachable, so this asserts the shape rather than the behaviour: every
    Protocol filled, every channel present, and the two agents that must not be
    able to fetch anything holding no toolbelt at all.
    """
    from app.agents.factory import build_dependencies, build_research_nodes
    from app.agents.researchers.router import ResearchRouter

    gateway, _, _ = fake.gateway()
    dependencies = build_dependencies(
        settings, gateway=gateway, database=database, storage=artifact_store
    )
    try:
        nodes = build_research_nodes(settings, dependencies=dependencies)
    finally:
        await dependencies.close()

    assert isinstance(nodes.researcher, ResearchRouter)
    assert nodes.researcher.channels == set(ResearchChannel)
    # Not a restricted belt, not an empty one: no attribute to reach for.
    for agent in (nodes.synthesizer, nodes.citation_validator):
        assert not hasattr(agent, "_toolbelt")
    assert nodes.citation_validator.__class__ is CitationValidator


async def test_every_agent_reaches_a_model_only_by_its_role(settings, database, artifact_store):
    """Model choice stays a routing policy, not a string inside a node (ADR 0007).

    Checked across the set so that a tenth agent added later cannot quietly
    bypass it: each one declares the role its calls are attributed to, and the
    roles are the ones the routing table knows.
    """
    from app.agents.factory import build_dependencies, build_research_nodes
    from app.core.enums import AgentName

    gateway, _, _ = fake.gateway()
    dependencies = build_dependencies(
        settings, gateway=gateway, database=database, storage=artifact_store
    )
    try:
        nodes = build_research_nodes(settings, dependencies=dependencies)
    finally:
        await dependencies.close()

    roles = {
        getattr(node, "role", None)
        for node in (
            nodes.planner,
            nodes.evidence_extractor,
            nodes.claim_normalizer,
            nodes.verifier,
            nodes.contradiction_checker,
            nodes.critic,
            nodes.synthesizer,
        )
    }
    assert roles == {
        AgentName.PLANNER,
        AgentName.EVIDENCE_EXTRACTOR,
        AgentName.CLAIM_NORMALIZER,
        AgentName.VERIFIER,
        AgentName.CRITIC,
        AgentName.SYNTHESIZER,
    }
    # The validator calls no model, so it has no role to route.
    assert not hasattr(nodes.citation_validator, "role")
