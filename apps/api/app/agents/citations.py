"""Citation markers: how a report cites, and how the citation is checked.

The two halves of the loop share this file because they share one thing that
must not drift: the numbering. The synthesizer is shown claims numbered from a
catalogue and writes ``[n]``; the validator rebuilds the *same* catalogue from
the same state and resolves every ``[n]`` through it. A difference of one
between them would make every citation in the report point at the wrong claim -
silently, and while still validating.

**The validator makes no model call.** TDD 4.2 allows "deterministic checks plus
one LLM repair pass", and the repair pass is the graph sending the draft back to
the synthesizer with instructions. The check itself is arithmetic over the state:
the marker resolves to a claim, the claim has evidence, the evidence has a
source, and the source was really retrieved. A validator with a model in it is a
validator that can be talked out of a rejection by the text it is validating.

The four reasons a citation is rejected are the four links of the chain, and
each is counted separately, because "the model cited claim 90 of 42" and "a
claim survived with no evidence behind it" are different defects with different
fixes.
"""

from __future__ import annotations

import re
from collections import Counter
from uuid import UUID

from app.agents.catalog import Catalog, claim_catalog
from app.agents.errors import AgentError
from app.agents.nodes import NodeResult
from app.agents.schemas import (
    CitationCheck,
    ClaimItem,
    EvidenceItem,
    NodeUsage,
    RejectionCount,
)
from app.agents.state import ResearchState
from app.core.logging import get_logger

logger = get_logger(__name__)

#: ``[n]`` as the frontend's Markdown renderer parses it, and as the report DTO
#: documents it. Bounded to three digits: a report with a thousand citations has
#: a different problem, and an unbounded run would match a line of a table.
CITATION_MARKER = re.compile(r"\[(\d{1,3})\]")

#: How many rejected markers a repair instruction names before it summarises.
_NAMED_REJECTIONS = 8


class CitationValidator:
    """Checks every ``[n]`` in a draft against the run's own records.

    Not a ``ModelAgent``: it has no prompt, no tools and no model. Its usage is
    genuinely zero, which is a measurement rather than a placeholder.
    """

    async def validate(self, state: ResearchState) -> NodeResult[CitationCheck]:
        report = state.get("report")
        if report is None:
            raise AgentError(
                "There is no draft to validate.",
                context={"research_id": str(state["research_id"])},
            )

        catalog = claim_catalog(state)
        claims = {claim.id: claim for claim in state.get("claims") or ()}
        evidence = {item.id: item for item in state.get("evidence") or ()}
        sources = {ref.source_id for ref in state.get("sources") or ()}

        checked = 0
        valid = 0
        reasons: Counter[str] = Counter()
        rejected_markers: list[int] = []

        for section in report.sections:
            for marker in cited_numbers(section.content_md):
                checked += 1
                reason = _reject(marker, catalog, claims, evidence, sources)
                if reason is None:
                    valid += 1
                    continue
                reasons[reason] += 1
                rejected_markers.append(marker)

        rejected = checked - valid
        logger.info(
            "citations validated",
            extra={
                "research_id": str(state["research_id"]),
                "revision": report.revision,
                "checked": checked,
                "valid": valid,
                "rejected": rejected,
                "reasons": dict(reasons),
                "claims_available": len(catalog),
            },
        )
        return NodeResult(
            value=CitationCheck(
                revision=report.revision,
                checked=checked,
                valid=valid,
                rejected=rejected,
                # Counted per reason and carried, not only logged: a report that
                # exhausts its repairs ships with these rejections, and the
                # reader is shown how many and why before the prose.
                rejections=tuple(
                    RejectionCount(reason=reason, count=count)
                    for reason, count in sorted(reasons.items())
                ),
                repair_instructions=(
                    _instructions(rejected_markers, reasons, catalog) if rejected else None
                ),
            ),
            usage=NodeUsage(),
        )


def cited_numbers(markdown: str) -> list[int]:
    """Every ``[n]`` in a section, in order, including repeats.

    Repeats are counted: each is a separate assertion resting on that claim, and
    a report that cites a broken marker five times has made five unsupported
    statements rather than one.

    Markdown links are not markers. ``[1](https://x)`` is a link whose label
    happens to be a number, and counting it would reject a legitimate link as a
    fabricated citation.
    """
    found: list[int] = []
    for match in CITATION_MARKER.finditer(markdown):
        after = markdown[match.end() : match.end() + 1]
        if after in ("(", "["):
            continue
        found.append(int(match.group(1)))
    return found


def _reject(
    marker: int,
    catalog: Catalog[ClaimItem],
    claims: dict[UUID, ClaimItem],
    evidence: dict[UUID, EvidenceItem],
    sources: set[UUID],
) -> str | None:
    """Why this citation does not resolve, or ``None`` if it does.

    The chain, link by link, in the order a citation is built: marker to claim,
    claim to evidence, evidence to source. The first broken link is the reason.
    """
    claim = catalog.get(marker)
    if claim is None:
        return "no_such_claim"
    if claim.id not in claims:
        # The catalogue is built from the state's claims, so this cannot happen
        # unless the two were built from different states. Checked anyway: it is
        # the exact failure the shared catalogue exists to prevent, and a silent
        # one.
        return "claim_not_in_run"
    backing = [evidence[eid] for eid in claim.evidence_ids if eid in evidence]
    if not backing:
        return "claim_without_evidence"
    if not any(item.source_id in sources for item in backing):
        return "evidence_without_retrieved_source"
    return None


def _instructions(markers: list[int], reasons: Counter[str], catalog: Catalog[ClaimItem]) -> str:
    """What the synthesizer is told to fix, specifically enough to act on.

    Names the offending numbers and the range that is valid. A repair prompt
    that only says "some citations were invalid" produces a second draft with
    the same citations in it.
    """
    offending = sorted(set(markers))
    named = ", ".join(f"[{marker}]" for marker in offending[:_NAMED_REJECTIONS])
    more = (
        f" and {len(offending) - _NAMED_REJECTIONS} more"
        if len(offending) > _NAMED_REJECTIONS
        else ""
    )
    explained = "; ".join(f"{_EXPLANATIONS[reason]} ({count})" for reason, count in reasons.items())
    valid_range = f"1 to {len(catalog)}" if catalog else "none - there are no claims to cite"
    return (
        f"{len(markers)} citation marker(s) in the draft did not resolve: {named}{more}. "
        f"Why: {explained}. Valid claim numbers are {valid_range}. "
        "Rewrite so that every factual sentence carries a marker from that range, "
        "and remove any sentence you cannot support with one rather than citing "
        "a different claim that does not say it."
    )


_EXPLANATIONS = {
    "no_such_claim": "the number is not in the list of claims you were given",
    "claim_not_in_run": "the claim is not part of this run",
    "claim_without_evidence": "the claim has no evidence behind it",
    "evidence_without_retrieved_source": "the evidence does not belong to a source "
    "this run retrieved",
}
