"""One ``Researcher`` over three, chosen by the subtask's channel.

The graph has a single researcher node and dispatches one ``Send`` per subtask,
so something has to decide which researcher a subtask gets. The planner proposes
- it is the step that read the question - and this resolves, because a proposal
from a model is a preference and not a fact about the deployment.

Resolution is deterministic and narrowing only:

* a subtask sent to a channel this deployment did not configure goes to the web,
  which is always present;
* a subtask sent to the documents when the run has no attached corpus goes to
  the web, because a document researcher with nothing to search returns nothing
  and the subtask is lost for the round.

Every resolution that changes a channel is logged with both, so a plan that
consistently asks for a channel the deployment lacks is visible rather than
silently rewritten.
"""

from __future__ import annotations

from app.agents.nodes import NodeResult, Researcher
from app.agents.schemas import ResearchChannel, SubtaskAssignment, TaskOutcome
from app.core.logging import get_logger

logger = get_logger(__name__)


class ResearchRouter:
    """Dispatches a subtask to the researcher for its channel."""

    def __init__(
        self,
        *,
        web: Researcher,
        documents: Researcher | None = None,
        data: Researcher | None = None,
    ) -> None:
        self._web = web
        self._by_channel: dict[ResearchChannel, Researcher] = {ResearchChannel.WEB: web}
        if documents is not None:
            self._by_channel[ResearchChannel.DOCUMENTS] = documents
        if data is not None:
            self._by_channel[ResearchChannel.DATA] = data

    @property
    def channels(self) -> frozenset[ResearchChannel]:
        """The channels this deployment can actually research."""
        return frozenset(self._by_channel)

    async def research(self, assignment: SubtaskAssignment) -> NodeResult[TaskOutcome]:
        asked = assignment.subtask.channel
        researcher = self._by_channel.get(asked)
        if researcher is None:
            logger.info(
                "a subtask asked for a channel this deployment does not have",
                extra={
                    "research_id": str(assignment.research_id),
                    "task_key": assignment.subtask.key,
                    "asked": asked.value,
                    "used": ResearchChannel.WEB.value,
                    "available": sorted(channel.value for channel in self._by_channel),
                },
            )
            researcher = self._web
        return await researcher.research(assignment)
