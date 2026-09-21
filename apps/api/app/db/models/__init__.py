"""ORM models for the system of record (docs/TDD.md section 7.2).

Every model is imported here so that ``Base.metadata`` is complete when Alembic
autogenerates a migration. A model that is not imported is a table that
silently never gets created.
"""

from app.db.models.answer import RunAnswerRow
from app.db.models.audit import AuditLogRow
from app.db.models.evaluation import EvaluationRow, FeedbackRow
from app.db.models.event import ResearchEventRow
from app.db.models.evidence import ClaimRow, ContradictionRow, EvidenceRow
from app.db.models.report import CitationRow, ReportRow, ReportSectionRow
from app.db.models.research import ResearchProjectRow, ResearchRunRow, ResearchTaskRow
from app.db.models.source import DocumentChunkRow, DocumentRow, SourceRow
from app.db.models.trace import AgentRunRow, LlmCallRow, ToolCallRow
from app.db.models.upload import ResearchRunUploadRow, UploadRow
from app.db.models.user import SessionRow, UserRow

__all__ = [
    "AgentRunRow",
    "AuditLogRow",
    "CitationRow",
    "ClaimRow",
    "ContradictionRow",
    "DocumentChunkRow",
    "DocumentRow",
    "EvaluationRow",
    "EvidenceRow",
    "FeedbackRow",
    "LlmCallRow",
    "ReportRow",
    "ReportSectionRow",
    "ResearchEventRow",
    "ResearchProjectRow",
    "ResearchRunRow",
    "ResearchRunUploadRow",
    "ResearchTaskRow",
    "RunAnswerRow",
    "SessionRow",
    "SourceRow",
    "ToolCallRow",
    "UploadRow",
    "UserRow",
]
