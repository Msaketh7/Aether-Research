"""Typed values carried in the research graph's state (Phase 9, ADR 0014).

Agents communicate through these, never through prose blobs, and every one of
them is written to a checkpoint after every node. Three consequences shape the
file:

* **Every type here must survive a checkpoint round trip unchanged.** LangGraph
  revives a stored Pydantic model only when its class is on the serializer's
  allowlist. A class that is not comes back as a plain ``dict``, and the only
  sign is a log line - so a node would fail after a resume, far from the cause.
  The allowlist is derived from these declarations (``app.agents.checkpoint``),
  and a test round-trips each type through the real serializer.
* **They are frozen and closed.** State changes by returning new values. A
  mutation made inside a node that then fails would otherwise leak into the
  retry.
* **Every collection is bounded.** A planner that proposes two hundred subtasks,
  or a synthesizer that returns a thousand sections, is refused here rather than
  discovered downstream as a runaway.

These are graph values, not API DTOs. A run's public shape lives in
``app.research.schemas``; the phases that persist claims and reports (11, 12)
map these onto rows.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.enums import (
    AgentName,
    ClaimStatus,
    ClaimType,
    EvidenceStance,
    ReportSectionKind,
    ResearchMode,
    SourceType,
    TaskPriority,
)
from app.research.schemas import (
    MAX_DEPTH,
    MAX_DOMAINS,
    MAX_QUESTION_LENGTH,
    MIN_DEPTH,
    RunLimits,
)

#: Subtasks one plan may propose. The number dispatched per round is a setting
#: (``max_subtasks_per_iteration``), which may not exceed this.
MAX_PLANNED_SUBTASKS = 20
MAX_SOURCES_PER_TASK = 100
#: Searches one subtask may report having issued. The run's query ceiling is
#: what actually bounds them; this is the shape a checkpoint may hold.
MAX_QUERIES_PER_TASK = 25
MAX_EVIDENCE_PER_CLAIM = 50
MAX_REPORT_SECTIONS = 20
MAX_CLAIMS_PER_SECTION = 500
#: The direct answer's ceiling. Generous enough for four or five paragraphs and
#: hard enough that a model which starts writing the report instead is cut off
#: rather than checkpointed.
MAX_ANSWER_CHARS = 12_000

#: A planner-assigned slug, as ``research_tasks.external_id`` stores it.
TASK_KEY_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,79}$"
#: Stable, greppable error codes, like ``AppError.code``.
ERROR_CODE_PATTERN = r"^[a-z][a-z0-9_]{0,59}$"

#: The anchor of a clock that has never advanced. Any real "resumed at" is later,
#: so a fresh clock measures from the moment the run was picked up.
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class GraphNode(StrEnum):
    """The research graph's nodes. The values are the LangGraph node names."""

    PLANNER = "planner"
    RESEARCHER = "researcher"
    EVIDENCE_EXTRACTOR = "evidence_extractor"
    CLAIM_NORMALIZER = "claim_normalizer"
    VERIFIER = "verifier"
    CONTRADICTION_CHECKER = "contradiction_checker"
    CRITIC = "critic"
    ANSWERER = "answerer"
    SYNTHESIZER = "synthesizer"
    CITATION_VALIDATOR = "citation_validator"

    @property
    def agent(self) -> AgentName:
        """The role this node's model calls and trace rows are attributed to.

        Contradiction checking is a node of its own - it has its own boundary,
        where the budget and the cancel flag are checked - but not a role of its
        own. It is the second half of verification, and ``AgentName`` is a
        vocabulary shared with the database and the frontend, so it is not
        widened for a distinction no consumer needs.
        """
        if self is GraphNode.CONTRADICTION_CHECKER:
            return AgentName.VERIFIER
        return AgentName(self.value)

    @property
    def discovers(self) -> bool:
        """Whether this node goes looking for more material.

        Discovery is what a reached limit stops. Everything after it - turning
        gathered material into claims, and claims into a report - still runs,
        so a limited run ends with a partial report rather than none.
        """
        return self in _DISCOVERY_NODES


_DISCOVERY_NODES = frozenset({GraphNode.PLANNER, GraphNode.RESEARCHER})


class ResearchChannel(StrEnum):
    """Where a subtask is researched (Phase 10).

    One node runs every subtask, so the node has to know which researcher to
    be. The planner proposes a channel - it is the only step that has read the
    question - and the router resolves it against what the run actually has: a
    subtask sent to ``DOCUMENTS`` for a run with no attached corpus is moved to
    the web rather than answered with nothing.
    """

    #: Search the open web, fetch and read pages.
    WEB = "web"
    #: The run's own attached documents, through retrieval. No network.
    DOCUMENTS = "documents"
    #: The structured sources: SEC filings, arXiv papers, GitHub repositories.
    DATA = "data"


class StopReason(StrEnum):
    """Why a run stopped looking for material before its critic was satisfied.

    When a second reason arises after the first, the first is kept: it is the
    explanation. The one exception is cancellation, which overrides - a
    cancelled run stopped because someone asked it to, whatever else was true.
    """

    CANCELLED = "cancelled"
    COST = "cost"
    COST_UNMEASURED = "cost_unmeasured"
    RUNTIME = "runtime"
    SOURCES = "sources"
    SEARCH_QUERIES = "search_queries"
    ITERATIONS = "iterations"
    #: A later planning round, or the critic, failed. Discovery cannot be
    #: steered without them, so it ends and what exists is reported.
    DISCOVERY_FAILED = "discovery_failed"


class GraphValue(BaseModel):
    """Base for every value the graph stores: frozen, closed."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# --- the run's allowance ------------------------------------------------------


class RunBudget(GraphValue):
    """The FR-8 ceilings as the graph enforces them."""

    max_iterations: int = Field(ge=1)
    max_sources: int = Field(ge=1)
    max_search_queries: int = Field(ge=1)
    max_runtime_seconds: int = Field(ge=1)
    max_cost_usd: float = Field(gt=0)

    @classmethod
    def from_limits(cls, limits: RunLimits) -> RunBudget:
        """From the limits frozen on the run when it was created.

        Built field for field from the DTO, and closed: if ``RunLimits`` gains a
        ceiling this does not know, construction fails rather than dropping it.
        """
        return cls.model_validate(limits.model_dump())


class RunParameters(GraphValue):
    """How the run was asked to research, as distinct from what it was asked.

    Grouped into one model rather than spread across the top of the state, for a
    reason found by running the Postgres checkpointer: it stores any top-level
    value that is an instance of ``str``, ``int``, ``float`` or ``bool`` inline in
    a JSON column, so a ``StrEnum`` there is written as its text and read back as
    a plain string. Inside a model it is stored as the model and validated back
    into the enum.
    """

    mode: ResearchMode
    depth: int = Field(ge=MIN_DEPTH, le=MAX_DEPTH)
    domains: tuple[str, ...] = Field(default=(), max_length=MAX_DOMAINS)
    date_range_start: date | None = None
    date_range_end: date | None = None
    parent_research_id: UUID | None = None
    #: Whether the user attached documents to this run. A fact about the run
    #: rather than a derived one: the planner decides whether to send a subtask
    #: to the document channel before any retrieval has happened, and a planner
    #: that had to ask the database would be a planner that can fail on a
    #: database. The worker sets it when it builds the brief (Phase 13).
    has_attached_documents: bool = False


class Stop(GraphValue):
    """Why discovery ended early, and the caveat the report carries for it.

    One value rather than two fields, so a reason and its caveat cannot disagree,
    and - like ``RunParameters`` - so the reason survives the Postgres checkpointer
    as an enum. As a bare top-level ``StrEnum`` it came back as a string, and
    ``stop is StopReason.CANCELLED`` quietly stopped matching.
    """

    reason: StopReason
    #: ``None`` for a cancelled run, which produces no report to carry it.
    caveat: str | None = Field(default=None, max_length=2000)


class TokenCount(GraphValue):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: TokenCount) -> TokenCount:
        return TokenCount(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
        )


class CostEstimate(GraphValue):
    """Estimated spend, and whether all of it could be measured.

    ``uncosted_calls`` counts model calls whose price is not declared. Their
    cost is unknown, not zero, and a total that silently left them out would
    read as authoritative when it is not.
    """

    usd: float = Field(default=0.0, ge=0)
    uncosted_calls: int = Field(default=0, ge=0)

    @property
    def measured(self) -> bool:
        return self.uncosted_calls == 0

    def __add__(self, other: CostEstimate) -> CostEstimate:
        return CostEstimate(
            usd=round(self.usd + other.usd, 6),
            uncosted_calls=self.uncosted_calls + other.uncosted_calls,
        )


class NodeUsage(GraphValue):
    """What one node consumed. Reported by the node, summed by the graph."""

    tokens: TokenCount = Field(default_factory=TokenCount)
    cost: CostEstimate = Field(default_factory=CostEstimate)
    search_queries: int = Field(default=0, ge=0)


class RunClock(GraphValue):
    """Active research time, anchored so that downtime is never counted.

    ``elapsed_seconds`` is the active time as of ``as_of``. The time now is that
    plus the time since the *later* of ``as_of`` and the moment this process
    picked the run up. So the minutes a run spends waiting for a crashed worker
    to be replaced do not count against its runtime ceiling - only the work
    lost in the crash does, and that is bounded by one node.
    """

    elapsed_seconds: float = Field(default=0.0, ge=0)
    as_of: datetime = EPOCH

    @field_validator("as_of")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("A run clock needs a timezone-aware timestamp.")
        return value

    def elapsed_at(self, now: datetime, *, resumed_at: datetime) -> float:
        anchor = max(self.as_of, resumed_at)
        return self.elapsed_seconds + max(0.0, (now - anchor).total_seconds())

    def advanced_to(self, now: datetime, *, resumed_at: datetime) -> RunClock:
        return RunClock(elapsed_seconds=self.elapsed_at(now, resumed_at=resumed_at), as_of=now)


# --- planning -----------------------------------------------------------------


class Subtask(GraphValue):
    """One planner subtask (FR-3). ``key`` is unique within its iteration."""

    key: str = Field(pattern=TASK_KEY_PATTERN)
    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    priority: TaskPriority
    iteration: int = Field(ge=1)
    rationale: str = Field(default="", max_length=2000)
    #: Resolved by the router before dispatch, never used as the planner gave
    #: it. Defaulted so that a checkpoint written before Phase 10 still loads.
    channel: ResearchChannel = ResearchChannel.WEB


class Plan(GraphValue):
    """A planning round's output.

    Empty is allowed: a re-plan that finds nothing worth researching ends
    discovery, and that is a legitimate answer rather than an error.
    """

    research_goal: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    iteration: int = Field(ge=1)
    subtasks: tuple[Subtask, ...] = Field(default=(), max_length=MAX_PLANNED_SUBTASKS)

    @model_validator(mode="after")
    def _consistent(self) -> Plan:
        keys = [subtask.key for subtask in self.subtasks]
        if len(keys) != len(set(keys)):
            raise ValueError("A plan must not name the same subtask twice.")
        if any(subtask.iteration != self.iteration for subtask in self.subtasks):
            raise ValueError("Every subtask in a plan belongs to that plan's iteration.")
        return self


class SubtaskAssignment(GraphValue):
    """One researcher's work order: a subtask, and its share of what is left.

    Carries what a researcher needs and nothing else. It runs in parallel with
    its siblings and must not read their progress from the shared state, so its
    allowances are decided before it starts.
    """

    research_id: UUID
    user_id: UUID
    query: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)
    #: The run's mode, carried rather than looked up: it decides which model
    #: tier the researcher's calls route to, and a researcher that defaulted it
    #: would spend deep-run prices inside a quick run.
    mode: ResearchMode
    subtask: Subtask
    query_allowance: int = Field(ge=1)
    source_allowance: int = Field(ge=1)
    #: What is left of the run's runtime ceiling. The researcher's timeout is the
    #: smaller of this and the per-node ceiling.
    time_allowance_seconds: float = Field(gt=0)
    domains: tuple[str, ...] = Field(default=(), max_length=MAX_DOMAINS)
    date_range_start: date | None = None
    date_range_end: date | None = None


# --- researching ----------------------------------------------------------------


class SourceRef(GraphValue):
    """A source a researcher recorded. The row itself lives in ``sources``.

    The last three fields are what the progress stream says about a source
    (Phase 14). They are carried here rather than read back from the row
    because the worker announces a source at the node boundary that found it,
    and a query per source to describe work the researcher had just done would
    be a query the researcher already paid for. Every one is defaulted: a
    checkpoint written before Phase 14 still loads, and reports what it knows.
    """

    source_id: UUID
    task_key: str = Field(pattern=TASK_KEY_PATTERN)
    title: str = Field(max_length=1000)
    url: str = Field(min_length=1, max_length=4096)
    source_type: SourceType = SourceType.WEB
    #: The registrable domain, or the name the finding API gave - never a name
    #: taken from the document, for the reason ``SourceDescriptor`` states.
    publisher: str = Field(default="", max_length=300)
    #: Chunks the ingester produced. 0 also means "not recorded", which is why
    #: the event that carries it is about progress rather than about size.
    chunk_count: int = Field(default=0, ge=0)


class TaskOutcome(GraphValue):
    task_key: str = Field(pattern=TASK_KEY_PATTERN)
    iteration: int = Field(ge=1)
    sources: tuple[SourceRef, ...] = Field(default=(), max_length=MAX_SOURCES_PER_TASK)
    #: The searches this subtask actually issued, in the order they were
    #: written. Carried so the stream can report a real query rather than the
    #: subtask's question standing in for one (Phase 14); bounded by the
    #: run's own query ceiling long before it reaches this length.
    queries: tuple[str, ...] = Field(default=(), max_length=MAX_QUERIES_PER_TASK)

    @model_validator(mode="after")
    def _own_sources(self) -> TaskOutcome:
        if any(source.task_key != self.task_key for source in self.sources):
            raise ValueError("A task outcome may only report sources found for that task.")
        return self


class NodeError(GraphValue):
    """A node that failed, recorded rather than thrown where the run can go on.

    ``message`` is for a person reading the trace. It must never carry retrieved
    content - a failing node's input may be a hostile page (ADR 0011) - which is
    why the graph writes it, not the node.
    """

    node: GraphNode
    iteration: int = Field(ge=0)
    code: str = Field(pattern=ERROR_CODE_PATTERN)
    message: str = Field(min_length=1, max_length=1000)
    task_key: str | None = Field(default=None, pattern=TASK_KEY_PATTERN)


# --- evidence, claims, contradictions -------------------------------------------


class EvidenceItem(GraphValue):
    """A verbatim span that bears on a claim. Offsets point into the stored
    document's normalised text, which is what makes a citation checkable."""

    id: UUID
    task_key: str = Field(pattern=TASK_KEY_PATTERN)
    iteration: int = Field(ge=1)
    source_id: UUID
    document_id: UUID
    #: The span itself, verbatim. Stored as ``evidence.span_text``.
    claim_text: str = Field(min_length=1, max_length=2000)
    span_start: int = Field(ge=0)
    span_end: int = Field(ge=1)
    stance: EvidenceStance
    #: The model that read the passage and named this span, as the provider
    #: reported it - not the model the role routes to, which failover can change
    #: mid-run. Stored as ``evidence.extractor_model``, which is how a span whose
    #: quality is later doubted can be traced to what produced it. Defaulted so a
    #: checkpoint written before Phase 11 still loads; such a span is recorded as
    #: ``unknown`` rather than attributed to a model that may not have made it.
    extractor_model: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def _ordered(self) -> EvidenceItem:
        if self.span_end <= self.span_start:
            raise ValueError("An evidence span must end after it starts.")
        # The offsets must bracket exactly this text in the stored document, or
        # re-reading the span at them would not return the quote and every
        # citation resting on it would be unverifiable. Phase 10 found that an
        # extractor is the one place this can go wrong silently, so it is an
        # invariant of the value rather than a rule an extractor remembers.
        if self.span_end - self.span_start != len(self.claim_text):
            raise ValueError(
                "An evidence span's offsets must bracket exactly its text: "
                f"{self.span_end - self.span_start} characters spanned, "
                f"{len(self.claim_text)} quoted."
            )
        return self


class ClaimItem(GraphValue):
    """An atomic statement. Never without evidence: a claim with nothing behind
    it could only ever be cited to nothing."""

    id: UUID
    normalized_key: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=2000)
    claim_type: ClaimType
    status: ClaimStatus = ClaimStatus.CANDIDATE
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: tuple[UUID, ...] = Field(min_length=1, max_length=MAX_EVIDENCE_PER_CLAIM)
    #: What this claim asserts, as distinct from what it is about. The key holds
    #: ``subject | predicate | qualifier`` and deliberately not the value, so two
    #: claims that disagree share a key; the value is what they disagree *about*,
    #: and a contradiction that could not name it would tell a reader only that a
    #: conflict exists. Taken verbatim from the claim's own text (the normalizer
    #: drops one that is not there), so it is never a second assertion.
    object_value: str = Field(default="", max_length=300)


class ContradictionItem(GraphValue):
    """Two claims that disagree. Recorded, never resolved silently (FR-7)."""

    id: UUID
    normalized_key: str = Field(min_length=1, max_length=300)
    claim_a_id: UUID
    claim_b_id: UUID
    likely_reason: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _two_claims(self) -> ContradictionItem:
        if self.claim_a_id == self.claim_b_id:
            raise ValueError("A claim cannot contradict itself.")
        return self


# --- judging and writing ----------------------------------------------------------


class MissingInfo(GraphValue):
    description: str = Field(min_length=1, max_length=1000)
    task_key: str | None = Field(default=None, pattern=TASK_KEY_PATTERN)


class Critique(GraphValue):
    """The critic's verdict on one round (FR-8)."""

    iteration: int = Field(ge=1)
    sufficient: bool
    missing: tuple[MissingInfo, ...] = Field(default=(), max_length=MAX_PLANNED_SUBTASKS)
    rationale: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _actionable(self) -> Critique:
        if not self.sufficient and not self.missing:
            raise ValueError(
                "An insufficient critique must say what is missing; "
                "otherwise another round has nothing to research."
            )
        return self


class ReportSectionDraft(GraphValue):
    kind: ReportSectionKind
    heading: str = Field(min_length=1, max_length=300)
    content_md: str = Field(min_length=1, max_length=100_000)
    claim_ids: tuple[UUID, ...] = Field(default=(), max_length=MAX_CLAIMS_PER_SECTION)


class ReportDraft(GraphValue):
    title: str = Field(min_length=1, max_length=300)
    #: The model that wrote this draft, as the provider reported it. Stored on
    #: the report row so a change in quality is attributable (TDD 7.2); not the
    #: role's configured model, which failover can differ from. Defaulted so a
    #: checkpoint written before Phase 12 still loads.
    model: str = Field(default="", max_length=120)
    #: 0 for the first draft; 1 after the one repair the validator may ask for.
    #: Set by the graph, not the synthesizer.
    revision: int = Field(ge=0)
    sections: tuple[ReportSectionDraft, ...] = Field(min_length=1, max_length=MAX_REPORT_SECTIONS)
    #: Set by the graph, not the synthesizer: when a limit ended discovery the
    #: caveat is a fact about the run, and a writer must not be able to drop it.
    coverage_caveat: str | None = Field(default=None, max_length=2000)


class AnswerDraft(GraphValue):
    """The direct answer to the question, as the reader saw it streamed.

    Held in state - and therefore checkpointed - for two reasons. A resumed run
    must not pay for the answer twice or stream a second one over the first;
    and the text has to outlive the stream, because the run is recorded from
    this state and a reader who opens the page an hour later never saw a single
    delta.

    ``claim_ids`` is derived from the ``[n]`` markers the model actually wrote,
    exactly as a report section's is, so it cannot list a claim the prose never
    used (ADR 0015).
    """

    text: str = Field(min_length=1, max_length=MAX_ANSWER_CHARS)
    #: The model that answered, as the provider reported it. Not the role's
    #: configured model: the chain's second entry answers whenever the first
    #: refused, and a quality question has to name the one that did.
    model: str = Field(default="", max_length=120)
    claim_ids: tuple[UUID, ...] = Field(default=(), max_length=MAX_CLAIMS_PER_SECTION)
    #: True when the model hit its output ceiling mid-sentence. Recorded rather
    #: than hidden: a reader is entitled to know the answer stops early.
    truncated: bool = False

    @property
    def word_count(self) -> int:
        return len(self.text.split())


class RejectionCount(GraphValue):
    """How many citations one broken link of the chain accounted for."""

    #: One of the validator's reason codes, which are the links of the chain.
    reason: str = Field(pattern=ERROR_CODE_PATTERN)
    count: int = Field(ge=1)


class CitationCheck(GraphValue):
    """The citation validator's verdict on one draft."""

    revision: int = Field(ge=0)
    checked: int = Field(ge=0)
    valid: int = Field(ge=0)
    rejected: int = Field(ge=0)
    #: Why the rejected ones were rejected, counted per reason. Carried in state
    #: rather than only logged because it is shown to the reader above a report
    #: that shipped with citations the run could not stand behind - which is the
    #: one case where the count is the most important thing on the page.
    rejections: tuple[RejectionCount, ...] = Field(default=(), max_length=8)
    repair_instructions: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _accounted(self) -> CitationCheck:
        if self.checked != self.valid + self.rejected:
            raise ValueError("Every checked citation is either valid or rejected.")
        if self.rejected and not self.repair_instructions:
            raise ValueError("Rejected citations need repair instructions for the synthesizer.")
        counted = sum(rejection.count for rejection in self.rejections)
        if self.rejections and counted != self.rejected:
            raise ValueError(
                "Every rejected citation belongs to exactly one reason: "
                f"{self.rejected} rejected, {counted} accounted for."
            )
        return self

    @property
    def passed(self) -> bool:
        return self.rejected == 0
