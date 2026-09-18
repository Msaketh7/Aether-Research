"""The ordinary run every scenario starts from, and the pieces that vary it.

A scenario should read as one sentence about what is different. So the corpus,
the plan, and an answer for each of the nine agents live here, and a scenario
overrides the one rule it is about.

**Answers derive from the prompt wherever a real model's would.** A model asked
to quote passage 7 has read the passages; a script that hard-codes "passage 1"
has instead guessed at retrieval's ranking, and goes red the day the ranking
improves. So ``quoting`` finds the passage that actually contains the quote and
answers with its number - which is what makes these tests about the system's
behaviour rather than about the order chunks came back in.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.agents.outputs import (
    ClaimsOutput,
    ClaimVerdict,
    ContradictionOutput,
    ContradictionPair,
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
from app.agents.schemas import ResearchChannel
from app.core.enums import (
    ClaimStatus,
    ClaimType,
    EvidenceStance,
    ReportSectionKind,
    TaskPriority,
)
from tests.scenarios.world import Page, ScriptedBrain, Web, prompt_text

QUESTION = "How do AI inference providers price hosted H100 capacity?"

#: The two pages the scripted web serves. Short on purpose: a page of eighty
#: words becomes a chunk or two at the test chunk size, so the sentence a
#: scenario quotes is whole inside one passage rather than split across two.
PRICE_LIST = Page(
    url="https://provider-a.test/pricing",
    title="Provider A inference pricing",
    body=(
        "Inference on H100 instances is priced at $4.10 per GPU-hour.\n\n"
        "Capacity is allocated quarterly and sold in one-year reservations. "
        "Discounts apply above five hundred committed hours per month."
    ),
    snippet="Provider A publishes list pricing for its H100 inference fleet.",
)
MARKET_REVIEW = Page(
    url="https://analyst-b.test/inference-review",
    title="The inference market in March",
    body=(
        "Provider A lists H100 capacity at $4.10 per GPU-hour as of March.\n\n"
        "Reservations remain the cheapest route for steady workloads, and "
        "spot capacity trades above list price during peak weeks."
    ),
    snippet="A review of published inference prices across the major providers.",
)

#: A third page that disagrees with the first about the same number. The
#: contradiction scenarios need two sources asserting the same thing with
#: different values, which is the only shape the checker is asked to judge.
DISPUTED_REVIEW = Page(
    url="https://analyst-c.test/inference-costs",
    title="What inference really costs",
    body=(
        "Provider A bills H100 capacity at $6.80 per GPU-hour in practice.\n\n"
        "List prices understate the cost once minimum commitments are included."
    ),
    snippet="An analysis of what providers charge once commitments are counted.",
)

QUOTES = {
    PRICE_LIST.url: "Inference on H100 instances is priced at $4.10 per GPU-hour.",
    MARKET_REVIEW.url: "Provider A lists H100 capacity at $4.10 per GPU-hour as of March.",
    DISPUTED_REVIEW.url: "Provider A bills H100 capacity at $6.80 per GPU-hour in practice.",
}

PRICE_KEY = "provider a | h100 gpu-hour price | 2026"

_PASSAGE_LABEL = re.compile(r"^--- passage (\d+) \|", re.MULTILINE)
_RESULT_LABEL = re.compile(r"^--- result (\d+) \| [^|]+ \| (\S+?) ---$", re.MULTILINE)


def web(*pages: Page) -> Web:
    """The scripted internet, serving these pages to every query."""
    return Web(pages=list(pages or (PRICE_LIST, MARKET_REVIEW)))


# --- reading the prompt -------------------------------------------------------------


def passage_holding(prompt: str, quote: str) -> int | None:
    """The number of the passage whose text contains ``quote``, if any."""
    matches = list(_PASSAGE_LABEL.finditer(prompt))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(prompt)
        if quote in prompt[match.end() : end]:
            return int(match.group(1))
    return None


def result_numbered(prompt: str, url: str) -> int | None:
    """The catalogue number a search result was offered under."""
    for match in _RESULT_LABEL.finditer(prompt):
        if match.group(2) == url:
            return int(match.group(1))
    return None


def offered_results(prompt: str) -> list[str]:
    """Every URL the selection prompt offered, in catalogue order."""
    return [match.group(2) for match in _RESULT_LABEL.finditer(prompt)]


# --- the nine answers ---------------------------------------------------------------


def plan(*questions: str, channel: ResearchChannel = ResearchChannel.WEB) -> PlanOutput:
    return PlanOutput(
        research_goal="Compare published H100 inference prices",
        subtasks=tuple(
            ProposedSubtask(question=question, priority=TaskPriority.HIGH, channel=channel)
            for question in questions
        ),
    )


def queries(*values: str) -> SearchQueries:
    return SearchQueries(queries=values or ("h100 gpu-hour list price 2026",))


def choosing(*urls: str):
    """Pick these URLs by the numbers the prompt offered them under.

    By URL rather than by position because a scenario is about which page was
    chosen, and the catalogue's order is retrieval's business. A URL that was
    not offered is simply not picked, which is how "the page was never in the
    results" reads as an empty selection rather than as an invented number.
    """

    def rule(request) -> SourceSelection:
        prompt = prompt_text(request)
        numbers = [number for url in urls if (number := result_numbered(prompt, url)) is not None]
        return SourceSelection(selected=tuple(SourceChoice(result=n) for n in numbers))

    return rule


def quoting(*quotes: str, stance: EvidenceStance = EvidenceStance.SUPPORTS):
    """Quote each of these, from whichever passage the prompt put it in."""

    def rule(request) -> EvidenceOutput:
        prompt = prompt_text(request)
        found = [
            EvidenceCandidate(passage=number, quote=quote, stance=stance)
            for quote in quotes
            if (number := passage_holding(prompt, quote)) is not None
        ]
        return EvidenceOutput(evidence=tuple(found))

    return rule


def claiming(
    *,
    text: str = "Provider A charges $4.10 per H100 GPU-hour.",
    key: str = PRICE_KEY,
    claim_type: ClaimType = ClaimType.QUANTITATIVE,
    confidence: float = 0.7,
):
    """One claim resting on every piece of evidence the prompt offered.

    Counting the catalogue rather than naming ``(1,)``: a claim that cites only
    the first span would make the corroboration scenarios silently untrue.
    """

    def rule(request) -> ClaimsOutput:
        prompt = prompt_text(request)
        numbers = tuple(int(n) for n in re.findall(r"^--- evidence (\d+) \|", prompt, re.MULTILINE))
        if not numbers:
            return ClaimsOutput(claims=())
        return ClaimsOutput(
            claims=(
                ProposedClaim(
                    text=text,
                    normalized_key=key,
                    claim_type=claim_type,
                    evidence=numbers,
                    confidence=confidence,
                ),
            )
        )

    return rule


def claiming_each(*pairs: tuple[str, str], key: str = PRICE_KEY):
    """One claim per evidence span, all sharing a key.

    What two sources disagreeing looks like to the normalizer: the same
    assertion, keyed the same way, with different values behind it - which is
    the only shape the contradiction checker is ever asked to judge.
    """

    def rule(request) -> ClaimsOutput:
        prompt = prompt_text(request)
        numbers = [int(n) for n in re.findall(r"^--- evidence (\d+) \|", prompt, re.MULTILINE)]
        claims = tuple(
            ProposedClaim(
                text=text,
                normalized_key=key,
                claim_type=ClaimType.QUANTITATIVE,
                object_value=value,
                evidence=(number,),
                confidence=0.6,
            )
            for number, (text, value) in zip(numbers, pairs, strict=False)
        )
        return ClaimsOutput(claims=claims)

    return rule


def verifying(status: ClaimStatus = ClaimStatus.VERIFIED, confidence: float = 0.9):
    """A verdict for every claim the prompt catalogued."""

    def rule(request) -> VerificationOutput:
        prompt = prompt_text(request)
        numbers = [int(n) for n in re.findall(r"^--- claim (\d+) \|", prompt, re.MULTILINE)]
        return VerificationOutput(
            verdicts=tuple(
                ClaimVerdict(claim=number, status=status, confidence=confidence)
                for number in numbers
            )
        )

    return rule


def no_contradictions() -> ContradictionOutput:
    return ContradictionOutput(contradictions=())


def contradicting(reason: str = "The two sources quote different periods."):
    """Report the first two claims the prompt offered as disagreeing."""

    def rule(request) -> ContradictionOutput:
        prompt = prompt_text(request)
        numbers = [int(n) for n in re.findall(r"^--- claim (\d+) \|", prompt, re.MULTILINE)]
        if len(numbers) < 2:
            return ContradictionOutput(contradictions=())
        return ContradictionOutput(
            contradictions=(
                ContradictionPair(claim_a=numbers[0], claim_b=numbers[1], likely_reason=reason),
            )
        )

    return rule


def critique(
    *, sufficient: bool, missing: str = "Pricing for the other providers"
) -> CritiqueOutput:
    return CritiqueOutput(
        sufficient=sufficient,
        missing=() if sufficient else (MissingItem(description=missing),),
        rationale="",
    )


def report(content: str = "Provider A charges $4.10 per H100 GPU-hour [1].") -> ReportOutput:
    return ReportOutput(
        title="H100 inference pricing",
        sections=(
            SectionOutput(
                kind=ReportSectionKind.EXECUTIVE_SUMMARY,
                heading="Summary",
                content_md=content,
            ),
        ),
    )


def citing_every_claim(prefix: str = "Published H100 pricing is consistent across sources"):
    """A report whose summary cites every claim the prompt catalogued.

    The synthesizer is the one agent whose output must reference state it was
    given, so deriving the markers from the catalogue is what keeps a scenario
    with two claims from writing a report that cites one and silently loses the
    other to the citation check.
    """

    def rule(request) -> ReportOutput:
        prompt = prompt_text(request)
        numbers = [int(n) for n in re.findall(r"^--- claim (\d+) \|", prompt, re.MULTILINE)]
        markers = " ".join(f"[{number}]" for number in numbers) or ""
        return report(f"{prefix} {markers}.".strip())

    return rule


# --- the whole script ---------------------------------------------------------------


def ordinary_run(
    *,
    subtasks: Sequence[str] = ("What does provider A charge per H100 GPU-hour?",),
    pick: Sequence[str] = (PRICE_LIST.url,),
    quotes: Sequence[str] = (QUOTES[PRICE_LIST.url],),
    sufficient: bool = True,
) -> ScriptedBrain:
    """A brain that answers every call a successful deep run makes.

    Registered by schema, so the answers hold however many researchers run at
    once and in whatever order they finish.
    """
    brain = ScriptedBrain()
    brain.on(PlanOutput, plan(*subtasks))
    brain.on(SearchQueries, queries())
    brain.on(SourceSelection, choosing(*pick))
    brain.on(EvidenceOutput, quoting(*quotes))
    brain.on(ClaimsOutput, claiming())
    brain.on(VerificationOutput, verifying())
    brain.on(ContradictionOutput, no_contradictions())
    brain.on(CritiqueOutput, critique(sufficient=sufficient))
    brain.on(ReportOutput, citing_every_claim())
    return brain
