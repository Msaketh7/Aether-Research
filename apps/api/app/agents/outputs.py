"""What a model is asked to return, as distinct from what the graph stores.

Two separate vocabularies on purpose. ``app.agents.schemas`` holds the run's
state: identified, checkpointed, and trusted by every node downstream. This
holds the shapes a *model* fills in, and nothing here is trusted until an agent
has checked it.

The rule that shapes every schema below: **a model never emits an identifier.**
It refers to things by their number in a catalogue the agent built and showed it
(``app.agents.catalog``). An index out of range is a fabrication the agent can
see and drop; a fabricated UUID is indistinguishable from a real one, and would
travel to a report as a citation to a source that was never retrieved.

Everything is closed (``extra="forbid"``) and bounded. A provider's structured
output mode is constrained decoding, not a guarantee: the value is validated
here regardless, and a field the model invented fails rather than being ignored.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.agents.schemas import (
    MAX_CLAIMS_PER_SECTION,
    MAX_EVIDENCE_PER_CLAIM,
    MAX_PLANNED_SUBTASKS,
    MAX_REPORT_SECTIONS,
    ResearchChannel,
)
from app.core.enums import (
    ClaimStatus,
    ClaimType,
    EvidenceStance,
    ReportSectionKind,
    TaskPriority,
)

#: Search queries one researcher may propose for one subtask. Its allowance may
#: be lower, and the smaller of the two wins - this is only the ceiling that
#: stops a model proposing forty.
MAX_QUERIES_PER_SUBTASK = 5
#: Candidate results a researcher may choose to fetch from one round of search.
MAX_SELECTED_SOURCES = 12
#: Evidence spans one extraction call may return.
MAX_EVIDENCE_PER_CALL = 40
#: Claims one normalization call may return.
MAX_CLAIMS_PER_CALL = 40
#: Contradictions one check may report.
MAX_CONTRADICTIONS_PER_CALL = 20
#: Claims one verification call may re-score.
MAX_VERDICTS_PER_CALL = 100

_MAX_REASON = 600
#: A span longer than this is not a span. The state permits 2000 characters;
#: an extractor that needs a thousand to make a point has quoted a page, not a
#: sentence, and the offsets it carries stop being useful to a reader.
MAX_QUOTE_CHARS = 1000


class AgentOutput(BaseModel):
    """Base for every model-filled value: closed, frozen, validated."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# --- planning -------------------------------------------------------------------


class ProposedSubtask(AgentOutput):
    """One subtask a planner proposes.

    No key: keys are assigned by the agent, derived from the iteration and the
    position, so two rounds cannot collide and a model cannot name a subtask
    something another round already used.
    """

    question: str = Field(min_length=5, max_length=500)
    priority: TaskPriority
    channel: ResearchChannel
    rationale: str = Field(default="", max_length=_MAX_REASON)


class PlanOutput(AgentOutput):
    research_goal: str = Field(min_length=5, max_length=1000)
    subtasks: tuple[ProposedSubtask, ...] = Field(default=(), max_length=MAX_PLANNED_SUBTASKS)


# --- researching ------------------------------------------------------------------


class SearchQueries(AgentOutput):
    """The queries a researcher wants to run for one subtask."""

    queries: tuple[str, ...] = Field(min_length=1, max_length=MAX_QUERIES_PER_SUBTASK)


class SourceChoice(AgentOutput):
    """One search result a researcher wants to read, by its catalogue number."""

    result: int = Field(ge=1)
    reason: str = Field(default="", max_length=_MAX_REASON)


class SourceSelection(AgentOutput):
    selected: tuple[SourceChoice, ...] = Field(default=(), max_length=MAX_SELECTED_SOURCES)


class DataSource(StrEnum):
    """The structured sources a data researcher may query.

    Narrower than ``SourceType`` deliberately: that vocabulary includes ``web``
    and ``upload``, and a model offered them here would be choosing a channel
    the router already decided.
    """

    SEC = "sec"
    ARXIV = "arxiv"
    GITHUB = "github"


class DataLookup(AgentOutput):
    """One query against one structured source.

    The narrowing fields are per-source and optional. They are accepted rather
    than required because a wrong one costs more than a missing one: an arXiv
    category that does not exist returns nothing, which reads as "no such
    research exists".
    """

    source: DataSource
    query: str = Field(min_length=2, max_length=250)
    #: SEC only, e.g. ``("10-K", "10-Q")``.
    forms: tuple[str, ...] = Field(default=(), max_length=6)
    #: arXiv only, e.g. ``cs.LG``.
    category: str | None = Field(default=None, max_length=40)


class DataQueries(AgentOutput):
    lookups: tuple[DataLookup, ...] = Field(min_length=1, max_length=MAX_QUERIES_PER_SUBTASK)


# --- evidence and claims -----------------------------------------------------------


class EvidenceCandidate(AgentOutput):
    """A span the extractor says bears on the question.

    ``quote`` is checked against the passage character for character before it
    becomes evidence. A quote that is not found there is dropped - it is either
    a paraphrase, which has no offsets, or an invention, which has no source.
    """

    passage: int = Field(ge=1)
    quote: str = Field(min_length=20, max_length=MAX_QUOTE_CHARS)
    stance: EvidenceStance


class EvidenceOutput(AgentOutput):
    evidence: tuple[EvidenceCandidate, ...] = Field(default=(), max_length=MAX_EVIDENCE_PER_CALL)


class ProposedClaim(AgentOutput):
    """One atomic statement, and the evidence catalogue numbers behind it."""

    text: str = Field(min_length=10, max_length=2000)
    #: Groups the same assertion across sources, which is what contradiction
    #: detection joins on. Normalised again by the agent, so two models writing
    #: "Nvidia / revenue / FY2025" and "nvidia revenue fy2025" still group.
    normalized_key: str = Field(min_length=3, max_length=200)
    #: What the claim asserts - the number, name or direction - quoted from its
    #: own text. The key holds the subject and period and deliberately not this,
    #: so that two sources disagreeing about a figure share a key; this is what
    #: they disagree about, and a contradiction cannot be displayed without it.
    #: Checked against the claim's text before it is kept, so it cannot become a
    #: second assertion the evidence was never asked about.
    object_value: str = Field(default="", max_length=300)
    claim_type: ClaimType
    evidence: tuple[int, ...] = Field(min_length=1, max_length=MAX_EVIDENCE_PER_CLAIM)
    confidence: float = Field(ge=0.0, le=1.0)


class ClaimsOutput(AgentOutput):
    claims: tuple[ProposedClaim, ...] = Field(default=(), max_length=MAX_CLAIMS_PER_CALL)


# --- verification --------------------------------------------------------------------


class ClaimVerdict(AgentOutput):
    """A re-scored claim, by its catalogue number."""

    claim: int = Field(ge=1)
    status: ClaimStatus
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(default="", max_length=_MAX_REASON)


class VerificationOutput(AgentOutput):
    verdicts: tuple[ClaimVerdict, ...] = Field(default=(), max_length=MAX_VERDICTS_PER_CALL)


class ContradictionPair(AgentOutput):
    claim_a: int = Field(ge=1)
    claim_b: int = Field(ge=1)
    #: Explicitly a hypothesis - "different fiscal periods" - and shown as one.
    likely_reason: str = Field(min_length=3, max_length=1000)


class ContradictionOutput(AgentOutput):
    contradictions: tuple[ContradictionPair, ...] = Field(
        default=(), max_length=MAX_CONTRADICTIONS_PER_CALL
    )


# --- judging and writing ----------------------------------------------------------------


class MissingItem(AgentOutput):
    description: str = Field(min_length=5, max_length=500)
    #: The subtask this gap belongs to, by catalogue number, where there is one.
    subtask: int | None = Field(default=None, ge=1)


class CritiqueOutput(AgentOutput):
    sufficient: bool
    missing: tuple[MissingItem, ...] = Field(default=(), max_length=MAX_PLANNED_SUBTASKS)
    rationale: str = Field(default="", max_length=2000)


class SectionOutput(AgentOutput):
    """One report section.

    No claim ids: the section cites claims with ``[n]`` markers in its Markdown,
    and the agent reads the markers back out. A model that also listed the ids
    could list one it did not cite, and the two would have to be reconciled -
    the text is the only thing a reader sees, so the text is the record.
    """

    kind: ReportSectionKind
    heading: str = Field(min_length=1, max_length=300)
    content_md: str = Field(min_length=1, max_length=100_000)


class ReportOutput(AgentOutput):
    title: str = Field(min_length=3, max_length=300)
    sections: tuple[SectionOutput, ...] = Field(min_length=1, max_length=MAX_REPORT_SECTIONS)


#: Cited claims one section may carry, mirroring the state schema's bound.
MAX_MARKERS_PER_SECTION = MAX_CLAIMS_PER_SECTION
