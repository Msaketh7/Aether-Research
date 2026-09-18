"""Closed vocabularies shared across modules.

These mirror the ``const`` arrays in ``@aether/shared-types/src/enums.ts`` and
the Postgres check constraints in ``docs/TDD.md`` section 7.2. All three must
agree; a contract test asserts the Python and TypeScript sides do.

``StrEnum`` so a value serialises as its own string and compares equal to it,
which keeps JSON payloads and SQL literals identical.
"""

from __future__ import annotations

from enum import StrEnum


class ResearchMode(StrEnum):
    QUICK = "quick"
    DEEP = "deep"
    CONVERSATIONAL = "conversational"


class RunStatus(StrEnum):
    QUEUED = "queued"
    PLANNING = "planning"
    RESEARCHING = "researching"
    VERIFYING = "verifying"
    SYNTHESIZING = "synthesizing"
    VALIDATING = "validating"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        """A terminal run will not change again; polling and streams stop."""
        return self in _TERMINAL_STATUSES

    @property
    def is_cancellable(self) -> bool:
        return not self.is_terminal


_TERMINAL_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED})


class ResearchEventType(StrEnum):
    """The progress vocabulary (ADR 0006).

    Mirrors ``RESEARCH_EVENT_TYPES`` in ``@aether/shared-types``, and
    ``research_events.type`` is constrained to it - a check constraint rather
    than prose, because an event the frontend cannot name is an event nobody
    sees.
    """

    RESEARCH_STARTED = "research_started"
    PLANNER_STARTED = "planner_started"
    PLANNER_COMPLETED = "planner_completed"
    SUBTASK_STARTED = "subtask_started"
    SEARCH_STARTED = "search_started"
    SOURCE_FOUND = "source_found"
    SOURCE_PROCESSED = "source_processed"
    SOURCES_PROGRESS = "sources_progress"
    CLAIM_EXTRACTED = "claim_extracted"
    EVIDENCE_PROGRESS = "evidence_progress"
    VERIFICATION_STARTED = "verification_started"
    CONTRADICTION_FOUND = "contradiction_found"
    CRITIC_STARTED = "critic_started"
    ADDITIONAL_RESEARCH_REQUESTED = "additional_research_requested"
    ITERATION_STARTED = "iteration_started"
    SYNTHESIS_STARTED = "synthesis_started"
    CITATION_CHECK = "citation_check"
    REPORT_COMPLETED = "report_completed"
    RESEARCH_FAILED = "research_failed"
    RESEARCH_CANCELLED = "research_cancelled"

    @property
    def is_terminal(self) -> bool:
        """After a terminal event the stream is closed deliberately."""
        return self in _TERMINAL_EVENTS


_TERMINAL_EVENTS = frozenset(
    {
        ResearchEventType.REPORT_COMPLETED,
        ResearchEventType.RESEARCH_FAILED,
        ResearchEventType.RESEARCH_CANCELLED,
    }
)


class TaskStatus(StrEnum):
    PENDING = "pending"
    RESEARCHING = "researching"
    DONE = "done"
    INSUFFICIENT = "insufficient"


class TaskPriority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class SourceType(StrEnum):
    WEB = "web"
    SEC = "sec"
    ARXIV = "arxiv"
    GITHUB = "github"
    UPLOAD = "upload"


class DocumentFormat(StrEnum):
    """What an ingested file was read as (Phase 7).

    Four formats, each with its own reader. The upload endpoint refuses anything
    else by name rather than guessing, and the check constraint on ``uploads``
    holds the same list.
    """

    PDF = "pdf"
    HTML = "html"
    MARKDOWN = "markdown"
    TEXT = "text"


class ClaimType(StrEnum):
    QUANTITATIVE = "quantitative"
    QUALITATIVE = "qualitative"
    EVENT = "event"


class ClaimStatus(StrEnum):
    CANDIDATE = "candidate"
    VERIFIED = "verified"
    REFUTED = "refuted"
    CONTESTED = "contested"


class EvidenceStance(StrEnum):
    SUPPORTS = "supports"
    REFUTES = "refutes"
    NEUTRAL = "neutral"


class ContradictionResolution(StrEnum):
    UNRESOLVED = "unresolved"
    RESOLVED_A = "resolved_a"
    RESOLVED_B = "resolved_b"
    BOTH_VALID_IN_CONTEXT = "both_valid_in_context"


class ReportStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    PUBLISHED = "published"


class ReportSectionKind(StrEnum):
    """Order of declaration is the order sections appear in a report (FR-9)."""

    EXECUTIVE_SUMMARY = "executive_summary"
    KEY_FINDINGS = "key_findings"
    DETAILED_ANALYSIS = "detailed_analysis"
    COMPETITIVE_LANDSCAPE = "competitive_landscape"
    EVIDENCE = "evidence"
    CONTRADICTIONS = "contradictions"
    CONFIDENCE_ASSESSMENT = "confidence_assessment"
    RECOMMENDATIONS = "recommendations"
    REFERENCES = "references"


class AgentName(StrEnum):
    PLANNER = "planner"
    RESEARCHER = "researcher"
    EVIDENCE_EXTRACTOR = "evidence_extractor"
    CLAIM_NORMALIZER = "claim_normalizer"
    VERIFIER = "verifier"
    CRITIC = "critic"
    SYNTHESIZER = "synthesizer"
    CITATION_VALIDATOR = "citation_validator"


class AgentStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"


class ToolName(StrEnum):
    SEARCH = "search"
    FETCH = "fetch"
    PARSE = "parse"
    RETRIEVE = "retrieve"
    SEC_API = "sec_api"
    ARXIV_API = "arxiv_api"
    GITHUB_API = "github_api"


class ToolStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"


class LlmProvider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OLLAMA = "ollama"


class LlmCallStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    FALLBACK = "fallback"


class EvaluationKind(StrEnum):
    RETRIEVAL = "retrieval"
    GENERATION = "generation"
    AGENT = "agent"
    INFRASTRUCTURE = "infrastructure"
    FULL = "full"


class FeedbackCategory(StrEnum):
    ACCURACY = "accuracy"
    COMPLETENESS = "completeness"
    CITATIONS = "citations"
    READABILITY = "readability"
    OTHER = "other"


class AuditAction(StrEnum):
    """What an audit row records (Phase 20).

    A closed vocabulary, so the log can be queried by action rather than by
    grepping free text, and so adding an auditable operation is a deliberate
    edit here rather than a new string invented at a call site.

    Two halves, per the threat model's repudiation control: authentication
    events, and the research mutations that change what a user owns. Reads are
    not audited - every request is already in the access log, and an audit
    trail that records everything records nothing.
    """

    LOGIN = "auth.login"
    LOGIN_FAILED = "auth.login_failed"
    LOGOUT = "auth.logout"
    REGISTER = "auth.register"
    REGISTER_REJECTED = "auth.register_rejected"
    SESSION_REVOKED = "auth.session_revoked"
    SESSIONS_REVOKED = "auth.sessions_revoked"
    SETTINGS_UPDATED = "account.settings_updated"

    RUN_CREATED = "research.run_created"
    RUN_CANCELLED = "research.run_cancelled"
    RUN_FOLLOWUP = "research.run_followup"
    FILE_UPLOADED = "research.file_uploaded"


class AuditOutcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
