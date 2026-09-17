"""The synthesizer: claims into the report a reader actually sees (FR-9).

The last model call, the strongest model, and the only output a user reads - so
this is where an unsupported sentence does the most damage and is hardest to
spot. Three constraints the agent applies around the model:

* **It may not write the reference list.** ``evidence`` and ``references``
  sections are refused here and assembled from the run's own records in Phase
  12. A model writing a list of sources is the single most reliable way to get
  citations to documents that do not exist, and the fix is to not ask it.
* **Section order is the enum's, not the model's.** ``ReportSectionKind``
  declares the order a report reads in (FR-9), so sections are sorted into it and
  a duplicate kind is merged rather than repeated.
* **Cited claims are read out of the text.** ``claim_ids`` is derived from the
  ``[n]`` markers the model actually wrote, so it cannot list a claim the prose
  never used or miss one it did.

The caveat and the revision are set by the graph afterwards, not here: both are
facts about the run rather than about the writing, and a writer must not be able
to drop the caveat a limit required.
"""

from __future__ import annotations

from uuid import UUID

from app.agents.base import AgentContext, ModelAgent
from app.agents.catalog import (
    Catalog,
    claim_catalog,
    contradiction_catalog,
    render_claims,
    render_contradictions,
)
from app.agents.citations import cited_numbers
from app.agents.errors import NoMaterialToWorkFrom
from app.agents.nodes import NodeResult
from app.agents.outputs import ReportOutput
from app.agents.prompting import render
from app.agents.schemas import (
    MAX_CLAIMS_PER_SECTION,
    MAX_REPORT_SECTIONS,
    ClaimItem,
    ContradictionItem,
    ReportDraft,
    ReportSectionDraft,
)
from app.agents.state import ResearchState
from app.core.enums import AgentName, ClaimStatus, ReportSectionKind
from app.core.logging import get_logger
from app.models.gateway import LLMGateway

logger = get_logger(__name__)

#: A full report. The largest output any agent asks for, and the one place where
#: a ceiling that is too low shows up as a report that stops mid-sentence.
MAX_REPORT_TOKENS = 16000

#: Sections assembled from records rather than written. See the module docstring.
ASSEMBLED_SECTIONS = frozenset({ReportSectionKind.EVIDENCE, ReportSectionKind.REFERENCES})

#: The order a report reads in, from the enum's declaration order (FR-9).
_SECTION_ORDER = {kind: position for position, kind in enumerate(ReportSectionKind)}


class SynthesisAgent(ModelAgent):
    """Writes the report from verified claims, and cites every factual sentence."""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        max_output_tokens: int = MAX_REPORT_TOKENS,
    ) -> None:
        super().__init__(gateway, role=AgentName.SYNTHESIZER, max_output_tokens=max_output_tokens)

    async def synthesize(self, state: ResearchState) -> NodeResult[ReportDraft]:
        context = AgentContext(
            research_id=state["research_id"],
            user_id=state["user_id"],
            mode=state["parameters"].mode,
            iteration=state.get("iteration", 0),
        )
        claims = claim_catalog(state)
        if not claims:
            # Raised rather than written around. The graph turns it into a
            # failed run, which is the honest outcome: a report with no claims
            # would be a page of prose with nothing behind any sentence, and
            # that is the artifact this system exists not to produce.
            raise NoMaterialToWorkFrom(
                "No claims were gathered, so there is nothing to write a report from.",
                context={
                    "research_id": str(state["research_id"]),
                    "sources": len(state.get("sources") or ()),
                    "evidence": len(state.get("evidence") or ()),
                },
            )

        contradictions = contradiction_catalog(state)
        prompt = render(
            "synthesizer",
            question=state["query"],
            scope=_scope(state, claims),
            claim_count=str(len(claims)),
            claims=render_claims(claims),
            contradictions=_contradictions(contradictions, claims),
            repair=_repair(state),
        )
        output, usage = await self.ask(context, prompt=prompt, schema=ReportOutput)

        sections = _sections(output, claims)
        if not sections:
            raise NoMaterialToWorkFrom(
                "The report had no usable sections.",
                context={
                    "research_id": str(state["research_id"]),
                    "returned": len(output.sections),
                },
            )
        logger.info(
            "report drafted",
            extra={
                "research_id": str(state["research_id"]),
                "returned_sections": len(output.sections),
                "kept_sections": len(sections),
                "kinds": [section.kind.value for section in sections],
                "citations": sum(len(section.claim_ids) for section in sections),
                "claims_offered": len(claims),
            },
        )
        return NodeResult(
            value=ReportDraft(
                title=output.title,
                # Overwritten by the graph, which owns both. Zero here rather
                # than a guess, so that a draft built outside a graph is
                # obviously a first draft.
                revision=0,
                sections=tuple(sections),
                coverage_caveat=None,
            ),
            usage=usage,
        )


def _sections(output: ReportOutput, claims: Catalog[ClaimItem]) -> list[ReportSectionDraft]:
    """The sections that may be kept, in the order a report reads in.

    A kind written twice is merged rather than dropped: a model that split its
    analysis across two blocks has written a longer section, not a duplicate
    one, and dropping the second would silently lose half the report.
    """
    merged: dict[ReportSectionKind, ReportSectionDraft] = {}
    for section in output.sections:
        if section.kind in ASSEMBLED_SECTIONS:
            logger.info(
                "a section the synthesizer may not write was dropped",
                extra={"kind": section.kind.value},
            )
            continue
        cited = _cited_claims(section.content_md, claims)
        draft = ReportSectionDraft(
            kind=section.kind,
            heading=section.heading,
            content_md=section.content_md,
            claim_ids=cited,
        )
        existing = merged.get(section.kind)
        if existing is not None:
            draft = existing.model_copy(
                update={
                    "content_md": f"{existing.content_md}\n\n{section.content_md}",
                    "claim_ids": tuple(dict.fromkeys((*existing.claim_ids, *cited)))[
                        :MAX_CLAIMS_PER_SECTION
                    ],
                }
            )
        merged[section.kind] = draft

    ordered = sorted(merged.values(), key=lambda section: _SECTION_ORDER[section.kind])
    return ordered[:MAX_REPORT_SECTIONS]


def _cited_claims(markdown: str, claims: Catalog[ClaimItem]) -> tuple[UUID, ...]:
    """The claims a section's markers actually resolve to, in first-cited order.

    Markers that resolve to nothing are deliberately left in the text. Scrubbing
    them here would make a fabricated citation disappear instead of being caught
    by the validator, and the repair loop exists precisely to catch it.
    """
    resolved: list[UUID] = []
    for marker in cited_numbers(markdown):
        claim = claims.get(marker)
        if claim is not None and claim.id not in resolved:
            resolved.append(claim.id)
    return tuple(resolved[:MAX_CLAIMS_PER_SECTION])


def _scope(state: ResearchState, claims: Catalog[ClaimItem]) -> str:
    """What the report is being written from, in counts rather than adjectives.

    Told to the writer so that the prose can be honest about its own basis -
    "drawn from four sources" reads very differently from "drawn from forty",
    and the writer is the only step that can say it.
    """
    all_claims = state.get("claims") or []
    unverified = sum(1 for claim in all_claims if claim.status is ClaimStatus.CANDIDATE)
    failed = len(state.get("failed_tasks") or ())
    lines = [
        f"- Sources retrieved: {len(state.get('sources') or ())}",
        f"- Evidence spans: {len(state.get('evidence') or ())}",
        f"- Claims available to cite: {len(claims)}"
        + (f", of which {unverified} are unverified candidates" if unverified else ""),
        f"- Research rounds run: {state.get('iteration', 0)}",
    ]
    if failed:
        lines.append(
            f"- {failed} subtask(s) returned nothing, so parts of the question may be uncovered"
        )
    stop = state.get("stop")
    if stop is not None and stop.caveat:
        lines.append(
            "- Research stopped at a limit. The report will carry a caveat saying so, "
            "added afterwards - do not write one yourself."
        )
    return "What this report is being written from:\n" + "\n".join(lines)


def _contradictions(contradictions: Catalog[ContradictionItem], claims: Catalog[ClaimItem]) -> str:
    if not contradictions:
        return "No contradictions were detected between the claims."
    return (
        f"Contradictions between these claims ({len(contradictions)}). Report both sides "
        "and say they disagree; do not choose between them:\n"
        + render_contradictions(contradictions, claims=claims)
    )


def _repair(state: ResearchState) -> str:
    """The validator's instructions, when this draft is a second attempt."""
    check = state.get("citation_check")
    if check is None or check.passed or not check.repair_instructions:
        return ""
    return (
        "This is a rewrite. The previous draft's citations were checked and some "
        f"did not resolve:\n{check.repair_instructions}"
    )
