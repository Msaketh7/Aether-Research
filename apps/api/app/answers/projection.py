"""Writing the run's answer onto its row.

The thinnest projection in the system, and the one with the fewest conditions on
it. The report's projection refuses to store a draft the citation validator has
not seen, because unverified prose behind the same endpoint as verified prose is
worse than nothing. The answer has no such gate: it is written before the
validator exists to run, it is what the reader has already been shown, and a
stored copy of what they read is not a claim that it was checked.

What the reader is told about verification lives elsewhere and stays there - the
report carries the citation check's verdict, and the run carries the caveat a
limit produced.
"""

from __future__ import annotations

import datetime as dt

from app.agents.state import ResearchState
from app.answers.repository import AnswerRecord, AnswerStore, answer_identity
from app.core.logging import get_logger

logger = get_logger(__name__)


class AnswerProjector:
    """Stores the answer a run wrote, if it wrote one."""

    def __init__(self, store: AnswerStore) -> None:
        self._store = store

    async def record(self, state: ResearchState, *, now: dt.datetime) -> AnswerRecord | None:
        """Project the run's answer, or ``None`` when it has none to project."""
        draft = state.get("answer")
        if draft is None:
            return None

        run_id = state["research_id"]
        record = AnswerRecord(
            id=answer_identity(run_id),
            run_id=run_id,
            content_md=draft.text,
            model=draft.model or "unknown",
            word_count=draft.word_count,
            citation_count=len(draft.claim_ids),
            truncated=draft.truncated,
            generated_at=now,
        )
        await self._store.record_answer(record)
        logger.debug(
            "answer projected",
            extra={
                "research_id": str(run_id),
                "words": record.word_count,
                "citations": record.citation_count,
            },
        )
        return record
