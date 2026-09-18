"""The web researcher: one subtask, searched, read and ingested.

Four steps, in this order, and each bounded before it runs:

1. **Write queries.** A model turns the subtask into search queries, at most its
   share of the run's query ceiling.
2. **Search.** One tool call per query, run together. What comes back is
   candidates - titles and snippets a page wrote about itself.
3. **Choose.** A model picks which candidates are worth a fetch, by catalogue
   number, at most its share of the run's source ceiling.
4. **Collect.** Each chosen page is fetched, parsed and ingested, becoming a
   source with a document behind it that evidence can point into.

The two model calls are separated for a reason that is about cost rather than
tidiness: query writing needs the subtask only, while choosing needs a page of
retrieved snippets. Folding them into one call would put untrusted content in
front of the model that decides what to search for, and would make every retry
of the selection re-pay for the query generation.

**The snippets are hostile input.** They reach the selection prompt only inside
a delimited untrusted block, and the model answers with numbers, so the worst a
malicious snippet can do is get its own page fetched - which is what it was
already asking for by being in the results. It cannot name a URL of its own.
"""

from __future__ import annotations

import asyncio
import datetime as dt

from app.agents.base import AgentContext, ModelAgent, total_usage, with_searches
from app.agents.catalog import Catalog
from app.agents.errors import NoMaterialToWorkFrom
from app.agents.nodes import NodeResult
from app.agents.outputs import (
    MAX_QUERIES_PER_SUBTASK,
    MAX_SELECTED_SOURCES,
    SearchQueries,
    SourceSelection,
)
from app.agents.prompting import render
from app.agents.researchers.brief import constraints, subtask_brief
from app.agents.researchers.collect import Candidate, SourceCollector
from app.agents.schemas import NodeUsage, SubtaskAssignment, TaskOutcome
from app.core.enums import AgentName, SourceType
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models.gateway import LLMGateway
from app.sources.toolbelt import Toolbelt
from app.sources.tools.search import SearchResult, WebSearchInput
from app.sources.untrusted import UntrustedPassage, UntrustedText, untrusted_block

logger = get_logger(__name__)

#: Results requested per query. More than a model will read, so that
#: deduplication and a bad query still leave something to choose from; fewer
#: than the tool's own ceiling of 25, which no subtask needs.
RESULTS_PER_QUERY = 10
#: A researcher writes queries and picks numbers. Neither is long.
MAX_RESEARCH_TOKENS = 1500
#: Candidates one selection prompt may carry. Bounded because a round of five
#: queries at ten results each is fifty snippets, and a prompt is not a corpus.
MAX_CANDIDATES = 40

_MAX_SNIPPET_CHARS = 400


class WebResearchAgent(ModelAgent):
    """Searches the open web for one subtask, within its allowances."""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        toolbelt: Toolbelt,
        collector: SourceCollector,
        results_per_query: int = RESULTS_PER_QUERY,
        max_output_tokens: int = MAX_RESEARCH_TOKENS,
    ) -> None:
        super().__init__(gateway, role=AgentName.RESEARCHER, max_output_tokens=max_output_tokens)
        self._toolbelt = toolbelt
        self._collector = collector
        self._results_per_query = results_per_query

    async def research(self, assignment: SubtaskAssignment) -> NodeResult[TaskOutcome]:
        context = _context(assignment)
        queries, query_usage = await self._queries(context, assignment)
        results, searched = await self._search(assignment, queries)
        if not results:
            logger.info(
                "web research found no candidates",
                extra={
                    "research_id": str(assignment.research_id),
                    "task_key": assignment.subtask.key,
                    "queries": len(queries),
                },
            )
            return NodeResult(
                value=_empty(assignment, tuple(queries)),
                usage=with_searches(query_usage, searched),
            )

        candidates = Catalog(_distinct(results)[:MAX_CANDIDATES])
        chosen, selection_usage = await self._select(context, assignment, candidates)
        collected = await self._collector.collect(
            chosen,
            research_id=assignment.research_id,
            task_key=assignment.subtask.key,
            limit=assignment.source_allowance,
        )
        logger.info(
            "web research finished",
            extra={
                "research_id": str(assignment.research_id),
                "task_key": assignment.subtask.key,
                "queries": len(queries),
                "searched": searched,
                "candidates": len(candidates),
                "chosen": len(chosen),
                "collected": len(collected.sources),
                "uncollectable": collected.failed,
            },
        )
        return NodeResult(
            value=TaskOutcome(
                task_key=assignment.subtask.key,
                iteration=assignment.subtask.iteration,
                sources=collected.sources,
                queries=tuple(queries),
            ),
            usage=with_searches(total_usage([query_usage, selection_usage]), searched),
        )

    # --- the steps ---------------------------------------------------------------

    async def _queries(
        self, context: AgentContext, assignment: SubtaskAssignment
    ) -> tuple[list[str], NodeUsage]:
        allowed = min(assignment.query_allowance, MAX_QUERIES_PER_SUBTASK)
        prompt = render(
            "researcher_queries",
            question=assignment.query,
            subtask=subtask_brief(assignment),
            constraints=constraints(assignment),
            max_queries=str(allowed),
        )
        output, usage = await self.ask(context, prompt=prompt, schema=SearchQueries)
        # Truncated rather than refused: a model that proposes one query too many
        # has still proposed good queries, and the ceiling is what matters.
        queries = [query.strip() for query in output.queries if query.strip()][:allowed]
        if not queries:
            raise NoMaterialToWorkFrom(
                "No search query could be written for this subtask.",
                context={"task_key": assignment.subtask.key},
            )
        return queries, usage

    async def _search(
        self, assignment: SubtaskAssignment, queries: list[str]
    ) -> tuple[list[SearchResult], int]:
        """Every query at once. Returns the results and how many searches ran.

        A query that fails is counted: the provider was asked. What is *not*
        counted here is the executor's retries of that one query, which the tool
        ledger records - this number is the run's search ceiling, which is about
        distinct questions asked rather than HTTP requests made.
        """
        recency = _recency_days(assignment)
        outcomes = await asyncio.gather(
            *(
                self._toolbelt.web_search(
                    WebSearchInput(
                        query=query[:400],
                        max_results=self._results_per_query,
                        recency_days=recency,
                        include_domains=assignment.domains,
                    )
                )
                for query in queries
            ),
            return_exceptions=True,
        )

        results: list[SearchResult] = []
        for _query, outcome in zip(queries, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                if not isinstance(outcome, Exception):  # pragma: no cover - cancellation
                    raise outcome
                logger.info(
                    "a search query failed",
                    extra={
                        "research_id": str(assignment.research_id),
                        "task_key": assignment.subtask.key,
                        "error_code": (outcome.code if isinstance(outcome, AppError) else None),
                        "error_type": type(outcome).__name__,
                    },
                )
                continue
            results.extend(outcome.value.results)
            logger.debug(
                "search returned",
                extra={"task_key": assignment.subtask.key, "results": len(outcome.value.results)},
            )
        return results, len(queries)

    async def _select(
        self,
        context: AgentContext,
        assignment: SubtaskAssignment,
        candidates: Catalog[SearchResult],
    ) -> tuple[list[Candidate], NodeUsage]:
        allowed = min(assignment.source_allowance, MAX_SELECTED_SOURCES)
        prompt = render(
            "researcher_select",
            subtask=subtask_brief(assignment),
            result_count=str(len(candidates)),
            results=_render_results(candidates),
            max_sources=str(allowed),
        )
        output, usage = await self.ask(context, prompt=prompt, schema=SourceSelection)

        picked, unknown = candidates.resolve([choice.result for choice in output.selected])
        if unknown:
            logger.warning(
                "the selector named results that were not offered",
                extra={
                    "research_id": str(assignment.research_id),
                    "task_key": assignment.subtask.key,
                    "offered": len(candidates),
                    "invented": list(unknown),
                },
            )
        return [
            Candidate(
                url=result.url,
                title=result.title,
                source_type=SourceType.WEB,
                publisher=result.domain,
            )
            for result in picked[:allowed]
        ], usage


# --- prompt fragments -------------------------------------------------------------


def _render_results(candidates: Catalog[SearchResult]) -> str:
    """Candidates as one delimited data block: a snippet is the page's own words."""
    return untrusted_block(
        [
            UntrustedPassage(
                label=f"result {number} | {result.domain} | {result.url}",
                text=UntrustedText(_summary(result)[:_MAX_SNIPPET_CHARS], source_url=result.url),
            )
            for number, result in candidates.numbered()
        ]
    )


def _distinct(results: list[SearchResult]) -> tuple[SearchResult, ...]:
    """One candidate per canonical URL, in the order the queries returned them.

    Several queries for one subtask return the same page, and the collector
    would collapse the duplicates anyway. Doing it before the catalogue is
    numbered means a selector cannot spend two of its picks on one page.
    """
    seen: set[str] = set()
    unique: list[SearchResult] = []
    for result in results:
        if result.canonical_url in seen:
            continue
        seen.add(result.canonical_url)
        unique.append(result)
    return tuple(unique)


def _summary(result: SearchResult) -> str:
    published = f" (published {result.published_at})" if result.published_at else ""
    return f"{result.title}{published}\n{result.snippet}"


def _recency_days(assignment: SubtaskAssignment) -> int | None:
    """The run's start date as a freshness filter, where a provider has one.

    ``None`` rather than a clamped value when the date is out of the tool's
    range: a ten-year-old start date is not a freshness filter, and sending the
    ceiling instead would quietly narrow the search to the last decade.
    """
    if assignment.date_range_start is None:
        return None
    days = (dt.date.today() - assignment.date_range_start).days
    return days if 1 <= days <= 3650 else None


def _context(assignment: SubtaskAssignment) -> AgentContext:
    return AgentContext(
        research_id=assignment.research_id,
        user_id=assignment.user_id,
        mode=assignment.mode,
        iteration=assignment.subtask.iteration,
        task_key=assignment.subtask.key,
    )


def _empty(assignment: SubtaskAssignment, queries: tuple[str, ...] = ()) -> TaskOutcome:
    return TaskOutcome(
        task_key=assignment.subtask.key,
        iteration=assignment.subtask.iteration,
        sources=(),
        # A subtask that searched and found nothing still searched, and the
        # stream reporting no query at all would read as a subtask that never
        # ran.
        queries=queries,
    )
