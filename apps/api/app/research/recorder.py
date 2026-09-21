"""What a finished run leaves behind.

The graph hands its final state here (``ResultRecorder`` in
``app.agents.runtime``), and this turns it into every row the product is served
from: the evidence chain first, then the report that cites it, and the answer
alongside them.

**One session, one transaction.** ``citations`` has foreign keys to ``claims``
and ``sources`` declared ``ON DELETE RESTRICT`` - the database enforcing the
product's promise that a citation cannot point at something that does not exist.
So the claims have to be written before the citations, and if the report fails to
write, the claims it would have cited must not be left behind as a run that half
finished. Two projectors, one unit of work.

**Order is a constraint, not a preference.** Evidence, then report. Reversing it
fails on the foreign key, which is the correct failure and an unhelpful place to
discover the intent. The answer has no such dependency - it references nothing -
so it is written first, where a failure to write the report cannot take it down
with it. That is the whole reason it is a row of its own.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from app.agents.state import ResearchState
from app.answers.projection import AnswerProjector
from app.core.logging import get_logger
from app.db.repositories.answers import SqlAlchemyAnswerRepository
from app.db.repositories.evidence import SqlAlchemyEvidenceRepository
from app.db.repositories.reports import SqlAlchemyReportRepository
from app.db.session import Database
from app.evidence.projection import EvidenceProjector, Projected
from app.reports.projection import ReportProjector

logger = get_logger(__name__)

#: Injected so a test can pin the timestamps a projection writes.
type Clock = Callable[[], dt.datetime]


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass(frozen=True, slots=True)
class Recorded:
    """What one run's projection wrote, for the worker's log line."""

    evidence: Projected
    #: ``None`` when the run never wrote an answer - it was cancelled, or its
    #: researchers found nothing to answer from.
    answer_words: int | None
    #: ``None`` when the run produced no validated report - a run that failed
    #: before synthesis, or before the citation check.
    sections: int | None
    citations: int | None


class RunRecorder:
    """Projects a finished run's evidence and report in one transaction."""

    def __init__(self, database: Database, *, now: Clock = utcnow) -> None:
        self._database = database
        self._now = now

    async def record(self, state: ResearchState) -> Recorded:
        """Write everything this run produced. Safe to call again."""
        # One timestamp for the whole projection, so a report and the claims it
        # cites agree about when the run was recorded.
        now = self._now()
        async with self._database.session() as session:
            answer = await AnswerProjector(SqlAlchemyAnswerRepository(session)).record(
                state, now=now
            )
            evidence = await EvidenceProjector(SqlAlchemyEvidenceRepository(session)).record(
                state, now=now
            )
            report = await ReportProjector(SqlAlchemyReportRepository(session)).record(
                state, now=now
            )
            await session.commit()

        recorded = Recorded(
            evidence=evidence,
            answer_words=None if answer is None else answer.word_count,
            sections=None if report is None else len(report.sections),
            citations=None if report is None else len(report.citations),
        )
        logger.info(
            "run recorded",
            extra={
                "research_id": str(state["research_id"]),
                "claims": evidence.claims,
                "evidence": evidence.evidence,
                "contradictions": evidence.contradictions,
                "answer_words": recorded.answer_words,
                "sections": recorded.sections,
                "citations": recorded.citations,
            },
        )
        return recorded
