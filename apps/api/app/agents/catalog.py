"""Numbered catalogues: how an agent shows a model the run's material.

A model is never given an identifier and never asked for one. It is given a
numbered list and answers with numbers, and the agent maps them back
(``app.agents.outputs``). That makes a fabricated reference *visible*: number 40
of a 12-item catalogue is out of range and is dropped, where a fabricated UUID
would have looked exactly like a real one all the way to the report.

Two properties every catalogue here has, because the whole scheme rests on them:

* **Deterministic.** Built by a pure function of the state, ordered by values
  that do not depend on which researcher finished first. The citation validator
  rebuilds the synthesizer's claim catalogue from the same state and has to get
  the same numbers, or every ``[n]`` in the report would resolve to something
  else.
* **Bounded.** A catalogue is capped, and what is dropped is the least useful
  by a stated rule rather than whatever fell off the end. A run that gathers two
  hundred claims must not build a prompt from all of them.

Retrieved text - a quote, a page title, a snippet - is rendered through
``untrusted_block``, never interpolated. What surrounds it here is written by
this system: catalogue numbers, ids, and URLs the SSRF guard already validated.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from uuid import UUID

from app.agents.schemas import (
    ClaimItem,
    ContradictionItem,
    EvidenceItem,
    SourceRef,
    Subtask,
)
from app.agents.state import ResearchState
from app.core.enums import ClaimStatus
from app.sources.untrusted import UntrustedPassage, UntrustedText, untrusted_block

#: Catalogue ceilings. Each is what one prompt may carry, not what a run may
#: hold: the state keeps everything, and a prompt gets the most useful slice.
MAX_PROMPT_SOURCES = 60
MAX_PROMPT_EVIDENCE = 80
MAX_PROMPT_CLAIMS = 120
MAX_PROMPT_SUBTASKS = 40
MAX_PROMPT_CONTRADICTIONS = 30

#: A quote longer than this is shortened *in the prompt only*; the stored span
#: keeps its offsets and its full text. Evidence spans are sentences, so this
#: bites only on a pathological one - and a prompt built from eighty of those
#: would be most of a context window.
MAX_PROMPT_QUOTE_CHARS = 800


@dataclass(frozen=True, slots=True)
class Catalog[T]:
    """Items numbered from 1, and the map back from a number a model returned."""

    items: tuple[T, ...]

    def __len__(self) -> int:
        return len(self.items)

    def __bool__(self) -> bool:
        return bool(self.items)

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)

    def numbered(self) -> Iterator[tuple[int, T]]:
        return enumerate(self.items, start=1)

    def get(self, number: int) -> T | None:
        """The item a model's number refers to, or ``None`` if it invented one."""
        if 1 <= number <= len(self.items):
            return self.items[number - 1]
        return None

    def resolve(self, numbers: Sequence[int]) -> tuple[tuple[T, ...], tuple[int, ...]]:
        """Split a model's numbers into the items they name and the ones that name nothing."""
        found: list[T] = []
        unknown: list[int] = []
        for number in numbers:
            item = self.get(number)
            if item is None:
                unknown.append(number)
            else:
                found.append(item)
        return tuple(found), tuple(unknown)


# --- building the catalogues ------------------------------------------------------


def source_catalog(state: ResearchState, *, limit: int = MAX_PROMPT_SOURCES) -> Catalog[SourceRef]:
    """Every source the run has gathered, oldest task first then by URL."""
    sources = state.get("sources") or []
    ordered = sorted(sources, key=lambda ref: (ref.task_key, ref.url, str(ref.source_id)))
    return Catalog(tuple(ordered[:limit]))


def subtask_catalog(state: ResearchState, *, limit: int = MAX_PROMPT_SUBTASKS) -> Catalog[Subtask]:
    """Every subtask planned so far, latest rounds last."""
    subtasks = state.get("subtasks") or []
    ordered = sorted(subtasks, key=lambda task: (task.iteration, task.key))
    return Catalog(tuple(ordered[-limit:]))


def evidence_catalog(
    state: ResearchState, *, limit: int = MAX_PROMPT_EVIDENCE
) -> Catalog[EvidenceItem]:
    """Evidence grouped by the source it came from, then by position in it.

    Grouping by source is what lets a normalizer see that two spans are the same
    page saying the same thing twice, rather than two independent witnesses.
    """
    evidence = state.get("evidence") or []
    ordered = sorted(
        evidence, key=lambda item: (str(item.source_id), item.span_start, str(item.id))
    )
    return Catalog(tuple(ordered[:limit]))


def claim_catalog(state: ResearchState, *, limit: int = MAX_PROMPT_CLAIMS) -> Catalog[ClaimItem]:
    """The run's claims, grouped by what they assert.

    Grouped by ``normalized_key`` so that claims about the same thing are
    adjacent - the contradiction check reads the catalogue looking for exactly
    that - and ordered within a group by id so the numbering never moves.

    Over the cap, the least useful go: refuted and contested claims are kept
    ahead of low-confidence candidates, because a report that omits a refutation
    is worse than one that omits a weak claim.
    """
    claims = state.get("claims") or []
    if len(claims) > limit:
        claims = sorted(claims, key=_claim_merit)[:limit]
    ordered = sorted(claims, key=lambda claim: (claim.normalized_key, str(claim.id)))
    return Catalog(tuple(ordered))


def contradiction_catalog(
    state: ResearchState, *, limit: int = MAX_PROMPT_CONTRADICTIONS
) -> Catalog[ContradictionItem]:
    contradictions = state.get("contradictions") or []
    ordered = sorted(contradictions, key=lambda item: (item.normalized_key, str(item.id)))
    return Catalog(tuple(ordered[:limit]))


#: Kept first when a claim catalogue has to be cut. A disagreement the system
#: found and then dropped from the prompt is the one omission a reader cannot
#: recover from.
_STATUS_MERIT = {
    ClaimStatus.REFUTED: 0,
    ClaimStatus.CONTESTED: 1,
    ClaimStatus.VERIFIED: 2,
    ClaimStatus.CANDIDATE: 3,
}


def _claim_merit(claim: ClaimItem) -> tuple[int, float, str]:
    return (_STATUS_MERIT[claim.status], -claim.confidence, str(claim.id))


# --- rendering them into a prompt ---------------------------------------------------


def render_subtasks(catalog: Catalog[Subtask]) -> str:
    """Subtasks as trusted text: this system wrote every character of them.

    A planner's question is model-written rather than retrieved, so it is not
    untrusted content in the threat model's sense - but it is also not something
    to concatenate carelessly, which is why it is one line per subtask with the
    newlines taken out.
    """
    return "\n".join(
        f"{number}. [{task.key}] ({task.priority.value}, {task.channel.value}) "
        f"{_oneline(task.question)}"
        for number, task in catalog.numbered()
    )


def render_sources(catalog: Catalog[SourceRef]) -> str:
    """Sources as a delimited data block: a page's title is the page's words."""
    return untrusted_block(
        [
            UntrustedPassage(
                label=f"source {number} | task {ref.task_key} | {ref.url}",
                text=UntrustedText(ref.title or "(untitled)", source_url=ref.url),
            )
            for number, ref in catalog.numbered()
        ]
    )


def render_evidence(
    catalog: Catalog[EvidenceItem],
    *,
    sources: Catalog[SourceRef],
) -> str:
    """Evidence spans as a delimited data block, each labelled with its source."""
    url = _url_lookup(sources)
    return untrusted_block(
        [
            UntrustedPassage(
                label=(
                    f"evidence {number} | source {_source_number(item.source_id, sources)} "
                    f"| stance {item.stance.value} | {url(item.source_id)}"
                ),
                text=UntrustedText(
                    item.claim_text[:MAX_PROMPT_QUOTE_CHARS], source_url=url(item.source_id)
                ),
            )
            for number, item in catalog.numbered()
        ]
    )


def render_claims(
    catalog: Catalog[ClaimItem],
    *,
    evidence: Catalog[EvidenceItem] | None = None,
    sources: Catalog[SourceRef] | None = None,
) -> str:
    """Claims as a delimited data block, optionally with the spans behind them.

    A claim's text is written by a model that was reading a hostile page, so it
    is quoted material once removed and is delimited like any other. The
    numbers, statuses and confidences around it are this system's own.
    """
    detail = _evidence_detail(evidence, sources)
    return untrusted_block(
        [
            UntrustedPassage(
                label=(
                    f"claim {number} | key {_oneline(claim.normalized_key)[:120]} "
                    f"| {claim.claim_type.value} | {claim.status.value} "
                    f"| confidence {claim.confidence:.2f}{detail(claim)}"
                ),
                text=UntrustedText(claim.text, source_url="aether://claim"),
            )
            for number, claim in catalog.numbered()
        ]
    )


def render_contradictions(
    catalog: Catalog[ContradictionItem],
    *,
    claims: Catalog[ClaimItem],
) -> str:
    return untrusted_block(
        [
            UntrustedPassage(
                label=(
                    f"contradiction {number} | claims "
                    f"{_claim_number(item.claim_a_id, claims)} and "
                    f"{_claim_number(item.claim_b_id, claims)}"
                ),
                text=UntrustedText(item.likely_reason, source_url="aether://contradiction"),
            )
            for number, item in catalog.numbered()
        ]
    )


def _evidence_detail(
    evidence: Catalog[EvidenceItem] | None,
    sources: Catalog[SourceRef] | None,
) -> Callable[[ClaimItem], str]:
    """A claim's evidence and source numbers, where both catalogues are to hand."""
    if evidence is None:
        return lambda _: ""
    numbers = {item.id: number for number, item in evidence.numbered()}
    origin = {item.id: item.source_id for item in evidence.items}

    def detail(claim: ClaimItem) -> str:
        cited = [numbers[eid] for eid in claim.evidence_ids if eid in numbers]
        if not cited:
            return " | evidence not shown"
        part = f" | evidence {', '.join(str(number) for number in sorted(cited))}"
        if sources is not None:
            found = {
                _source_number(origin[eid], sources)
                for eid in claim.evidence_ids
                if eid in origin and _source_number(origin[eid], sources) is not None
            }
            part += f" | {len(found)} source(s)"
        return part

    return detail


def _url_lookup(sources: Catalog[SourceRef]) -> Callable[[UUID], str]:
    urls = {ref.source_id: ref.url for ref in sources.items}
    return lambda source_id: urls.get(source_id, "aether://source-not-listed")


def _source_number(source_id: UUID, sources: Catalog[SourceRef]) -> int | None:
    return next((number for number, ref in sources.numbered() if ref.source_id == source_id), None)


def _claim_number(claim_id: UUID, claims: Catalog[ClaimItem]) -> int | None:
    return next((number for number, claim in claims.numbered() if claim.id == claim_id), None)


def _oneline(value: str) -> str:
    return " ".join(value.split())
