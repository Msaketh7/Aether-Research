"""Cross-language contract tests.

The frontend and the API are typed independently - TypeScript in
``packages/shared-types``, Pydantic here - and nothing in either compiler can
see the other. This test is the seam that keeps them honest: it reads the
TypeScript source and asserts the Python vocabularies match, so a value added
on one side and forgotten on the other fails a build instead of producing a
runtime surprise for a user.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.enums import (
    AgentName,
    AgentStatus,
    ClaimStatus,
    ClaimType,
    ContradictionResolution,
    EvaluationKind,
    EvidenceStance,
    FeedbackCategory,
    LlmProvider,
    ReportSectionKind,
    ReportStatus,
    ResearchMode,
    RunStatus,
    SourceType,
    TaskPriority,
    TaskStatus,
    ToolName,
    ToolStatus,
)
from app.research.events import ResearchEventType

SHARED_TYPES = Path(__file__).resolve().parents[3] / "packages" / "shared-types" / "src"


def ts_const_array(source: Path, name: str) -> list[str]:
    """Extract the members of ``export const NAME = [...] as const;``."""
    text = source.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = \[(.*?)\] as const;", text, re.DOTALL)
    if match is None:  # pragma: no cover - a missing constant is a hard failure
        pytest.fail(f"{name} not found in {source.name}")
    return re.findall(r"'([^']+)'", match.group(1))


ENUM_CASES = [
    ("enums.ts", "RESEARCH_MODES", ResearchMode),
    ("enums.ts", "RUN_STATUSES", RunStatus),
    ("enums.ts", "TASK_STATUSES", TaskStatus),
    ("enums.ts", "TASK_PRIORITIES", TaskPriority),
    ("enums.ts", "SOURCE_TYPES", SourceType),
    ("enums.ts", "CLAIM_TYPES", ClaimType),
    ("enums.ts", "CLAIM_STATUSES", ClaimStatus),
    ("enums.ts", "EVIDENCE_STANCES", EvidenceStance),
    ("enums.ts", "CONTRADICTION_RESOLUTIONS", ContradictionResolution),
    ("enums.ts", "REPORT_STATUSES", ReportStatus),
    ("enums.ts", "REPORT_SECTION_KINDS", ReportSectionKind),
    ("enums.ts", "AGENT_NAMES", AgentName),
    ("enums.ts", "AGENT_STATUSES", AgentStatus),
    ("enums.ts", "TOOL_NAMES", ToolName),
    ("enums.ts", "TOOL_STATUSES", ToolStatus),
    ("enums.ts", "LLM_PROVIDERS", LlmProvider),
    ("enums.ts", "EVALUATION_KINDS", EvaluationKind),
    ("enums.ts", "FEEDBACK_CATEGORIES", FeedbackCategory),
    ("events.ts", "RESEARCH_EVENT_TYPES", ResearchEventType),
]


@pytest.mark.parametrize(
    ("filename", "constant", "enum"), ENUM_CASES, ids=lambda v: getattr(v, "__name__", v)
)
def test_python_and_typescript_vocabularies_match(filename, constant, enum):
    typescript = ts_const_array(SHARED_TYPES / filename, constant)
    python = [member.value for member in enum]

    assert python == typescript, (
        f"{enum.__name__} and {constant} have diverged. Python: {python}. TypeScript: {typescript}."
    )


def test_report_sections_are_declared_in_the_order_they_appear():
    """FR-9 fixes the order of a report's sections; both sides encode it."""
    typescript = ts_const_array(SHARED_TYPES / "enums.ts", "REPORT_SECTION_KINDS")
    assert typescript[0] == "executive_summary"
    assert typescript[-1] == "references"
    assert [member.value for member in ReportSectionKind] == typescript


def test_terminal_statuses_agree_with_the_frontend():
    """The frontend stops polling on these; a mismatch would hang a run's UI."""
    text = (SHARED_TYPES / "enums.ts").read_text(encoding="utf-8")
    match = re.search(r"TERMINAL_RUN_STATUSES = \[(.*?)\] as const;", text, re.DOTALL)
    assert match is not None
    typescript = set(re.findall(r"'([^']+)'", match.group(1)))

    python = {status.value for status in RunStatus if status.is_terminal}
    assert python == typescript
