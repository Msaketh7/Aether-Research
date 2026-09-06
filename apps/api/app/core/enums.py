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
