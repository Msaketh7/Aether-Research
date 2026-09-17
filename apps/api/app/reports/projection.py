"""Writing the assembled report onto its rows (Phase 12).

The thin half of report generation: assembly (`app.reports.assembly`) decides
what the report says, this hands it to the store. It exists as its own step for
the same reason the evidence projection does - the graph's checkpoint is not a
read model, and a run that finished has nothing anyone can open until its report
is rows (ADR 0016).

**A draft is only projected once it has been checked.** A report whose citations
have not been through the validator is a draft the system has no opinion about,
and storing it would put unverified prose behind the same endpoint as verified
prose. The graph always validates before it finishes, so in practice this skips
only the run that failed before the validator ran - which is exactly the run
that should not have a report.
"""

from __future__ import annotations

import datetime as dt

from app.agents.catalog import claim_catalog
from app.agents.state import ResearchState
from app.core.logging import get_logger
from app.reports.assembly import AssembledReport, assemble
from app.reports.repository import ReportStore

logger = get_logger(__name__)


class ReportProjector:
    """Assembles a run's report and writes it, sections and citations together."""

    def __init__(self, store: ReportStore) -> None:
        self._store = store

    async def record(self, state: ResearchState, *, now: dt.datetime) -> AssembledReport | None:
        """Project the run's report, or ``None`` when it has none to project."""
        draft = state.get("report")
        check = state.get("citation_check")
        if draft is None or check is None:
            logger.debug(
                "no validated draft to project",
                extra={
                    "research_id": str(state["research_id"]),
                    "has_draft": draft is not None,
                    "has_check": check is not None,
                },
            )
            return None

        research_id = state["research_id"]
        assembled = assemble(
            draft,
            run_id=research_id,
            # Rebuilt from the same state the synthesizer and the validator saw,
            # which is what the draft's numbers mean. Three rebuilds of one
            # catalogue, and a difference between any two of them would renumber
            # every citation in the report while still validating (ADR 0015).
            claims=claim_catalog(state),
            evidence={item.id: item for item in state.get("evidence") or ()},
            sources=await self._store.cited_sources(research_id),
            retrieved={ref.source_id for ref in state.get("sources") or ()},
            check=check,
            now=now,
        )
        await self._store.record_report(assembled.report, assembled.sections, assembled.citations)
        return assembled
