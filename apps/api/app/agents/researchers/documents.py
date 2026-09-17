"""The document researcher: the run's own attached files, through retrieval.

No network, no tools, and nothing ingested - the documents were already stored
and chunked when they were uploaded. This researcher's job is to find which of
them bear on a subtask and report them as sources, so that the extractor
downstream has something to quote and a citation has somewhere to resolve.

Three consequences of it being retrieval rather than search:

* **It costs no search queries.** The run's search ceiling governs calls to the
  open web. Reading the user's own corpus is bounded by the run's time and cost
  ceilings and by the retriever's own limit, and charging it against the search
  ceiling would mean attaching documents made a run do less web research.
* **It is honest about an arm that did not run.** A corpus with no embeddings -
  no model configured, or vectors still pending - retrieves lexically, and the
  result says so rather than silently returning less. That reaches the log, so
  "the documents did not answer" can be told apart from "half the search did not
  happen".
* **It reports sources, not chunks.** A retrieved chunk belongs to a document
  which belongs to a source, and the source is what a report cites. Several
  chunks from one file are one source.
"""

from __future__ import annotations

from app.agents.base import AgentContext, ModelAgent
from app.agents.nodes import NodeResult
from app.agents.outputs import MAX_QUERIES_PER_SUBTASK, SearchQueries
from app.agents.prompting import render
from app.agents.schemas import SourceRef, SubtaskAssignment, TaskOutcome
from app.core.enums import AgentName
from app.core.logging import get_logger
from app.db.repositories.documents import SqlAlchemyDocumentRepository
from app.db.session import Database
from app.models.gateway import LLMGateway
from app.retrieval.results import RetrievedChunk
from app.retrieval.retriever import Retriever

logger = get_logger(__name__)

#: Retrieval calls one subtask may make. Each is two database queries and one
#: embedding, so this is a real bound rather than a formality.
MAX_RETRIEVALS_PER_SUBTASK = 3
MAX_QUERY_TOKENS = 800
_MAX_TITLE_CHARS = 500


class DocumentResearchAgent(ModelAgent):
    """Searches the documents attached to this run, and nothing else."""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        retriever: Retriever,
        database: Database,
        max_retrievals: int = MAX_RETRIEVALS_PER_SUBTASK,
        max_output_tokens: int = MAX_QUERY_TOKENS,
    ) -> None:
        super().__init__(gateway, role=AgentName.RESEARCHER, max_output_tokens=max_output_tokens)
        self._retriever = retriever
        self._database = database
        self._max_retrievals = max_retrievals

    async def research(self, assignment: SubtaskAssignment) -> NodeResult[TaskOutcome]:
        context = AgentContext(
            research_id=assignment.research_id,
            user_id=assignment.user_id,
            mode=assignment.mode,
            iteration=assignment.subtask.iteration,
            task_key=assignment.subtask.key,
        )
        allowed = min(self._max_retrievals, MAX_QUERIES_PER_SUBTASK)
        prompt = render(
            "researcher_queries",
            question=assignment.query,
            subtask=(
                f"{assignment.subtask.question} [{assignment.subtask.key}]\n"
                "These queries search the documents the user attached to this run, "
                "not the web."
            ),
            constraints=(
                "Write the queries in the words the attached documents would use. "
                "Domain and date filters do not apply to an attached corpus."
            ),
            max_queries=str(allowed),
        )
        output, usage = await self.ask(context, prompt=prompt, schema=SearchQueries)
        queries = [query.strip() for query in output.queries if query.strip()][:allowed]

        found: dict[str, SourceRef] = {}
        for query in queries:
            result = await self._retriever.retrieve(
                query,
                run_id=assignment.research_id,
                user_id=assignment.user_id,
            )
            for arm in result.arms:
                if arm.skipped:
                    logger.info(
                        "a retrieval arm did not run",
                        extra={
                            "research_id": str(assignment.research_id),
                            "task_key": assignment.subtask.key,
                            "arm": arm.strategy.value,
                            "reason": arm.skipped_reason,
                        },
                    )
            for hit in result.chunks:
                found.setdefault(str(hit.chunk.source_id), _ref(hit, assignment))

        sources = await self._titled(list(found.values()), assignment)
        logger.info(
            "document research finished",
            extra={
                "research_id": str(assignment.research_id),
                "task_key": assignment.subtask.key,
                "queries": len(queries),
                "sources": len(sources),
            },
        )
        return NodeResult(
            value=TaskOutcome(
                task_key=assignment.subtask.key,
                iteration=assignment.subtask.iteration,
                sources=tuple(sources[: assignment.source_allowance]),
            ),
            usage=usage,
        )

    async def _titled(
        self, refs: list[SourceRef], assignment: SubtaskAssignment
    ) -> list[SourceRef]:
        """Fill in each source's stored title.

        A retrieved chunk knows its source's id and URL but not its title, and a
        source reported without one would reach the report's reference list as a
        bare URL. One read, scoped by user, rather than one per chunk.
        """
        if not refs:
            return []
        async with self._database.session() as session:
            rows = await SqlAlchemyDocumentRepository(session).sources_by_id(
                [ref.source_id for ref in refs], user_id=assignment.user_id
            )
        titles = {row.id: row.title for row in rows}
        owned = set(titles)
        return [
            ref.model_copy(update={"title": titles[ref.source_id][:_MAX_TITLE_CHARS]})
            for ref in refs
            if ref.source_id in owned
        ]


def _ref(hit: RetrievedChunk, assignment: SubtaskAssignment) -> SourceRef:
    """A retrieved chunk as a source reference, titled later.

    The URL comes from the chunk's own untrusted text, which carries the source
    it was read from - the retriever puts it there so that a quote can never be
    separated from where it came from.
    """
    return SourceRef(
        source_id=hit.chunk.source_id,
        task_key=assignment.subtask.key,
        title="",
        url=hit.chunk.text.source_url,
    )
