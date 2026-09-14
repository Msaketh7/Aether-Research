"""Whether a run has been cancelled, as the research graph asks at every node.

The API cancels a run by writing ``status = cancelled``
(``ResearchService.cancel``). The graph reads the same column, so there is one
source of truth. TDD 11 sketches a Redis flag instead; a second store holding a
copy of this one bit would be a second thing that can disagree with it.

One primary-key read per node boundary. A run that no longer exists, or does
not belong to the user the graph is running for, reads as cancelled: there is
nobody left to deliver its report to, and carrying on would spend money on it.
"""

from __future__ import annotations

from uuid import UUID

from app.core.enums import RunStatus
from app.db.repositories.research import SqlAlchemyResearchRepository
from app.db.session import Database


class PostgresCancellationProbe:
    """Implements ``app.agents.graph.CancellationProbe`` over ``research_runs``."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def is_cancelled(self, research_id: UUID, user_id: UUID) -> bool:
        async with self._database.session() as session:
            status = await SqlAlchemyResearchRepository(session).status_of(
                research_id, user_id=user_id
            )
        return status is None or status is RunStatus.CANCELLED
