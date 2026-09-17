"""The data researcher: SEC filings, arXiv papers and GitHub repositories.

The same shape as the web researcher - decide what to look up, look it up,
collect what comes back - and different in the one way that matters: these
sources return *records*, not pages. A filing record already names its company,
form type and filing date; a paper record already names its authors and its
categories. That metadata comes from the API rather than from the document, so
it is worth keeping, and it is what goes into the source row.

So there is no selection call here. A search result on the open web is a page
advertising itself and needs judging before a fetch is spent on it; an EDGAR hit
is a filing that matches the query, and the ranking is the API's. The saving is
one model call per subtask, and what replaces the judgement is the narrowing in
the lookup itself - the form type, the category, the ranked order.

What is collected is the filing's or the paper's own landing page, fetched and
ingested like any other document, because a citation has to resolve to text this
system actually read. A GitHub repository is the exception: its record is the
description and the statistics, and there is no article to fetch, so a
repository is reported as a source pointing at the repository page.
"""

from __future__ import annotations

import asyncio
import datetime as dt

from app.agents.base import AgentContext, ModelAgent, with_searches
from app.agents.nodes import NodeResult
from app.agents.outputs import DataLookup, DataQueries, DataSource
from app.agents.prompting import render
from app.agents.researchers.brief import constraints, subtask_brief
from app.agents.researchers.collect import Candidate, SourceCollector
from app.agents.researchers.web import MAX_RESEARCH_TOKENS
from app.agents.schemas import SubtaskAssignment, TaskOutcome
from app.core.enums import AgentName, SourceType
from app.core.errors import AppError
from app.core.logging import get_logger
from app.models.gateway import LLMGateway
from app.sources.toolbelt import Toolbelt
from app.sources.tools.arxiv import SearchArxivInput, SearchArxivOutput
from app.sources.tools.github import SearchGithubInput, SearchGithubOutput
from app.sources.tools.sec import SearchSecInput, SearchSecOutput

logger = get_logger(__name__)

#: Records requested per lookup. Smaller than the web researcher's, because
#: these are ranked by the source rather than by an advertising market.
RECORDS_PER_LOOKUP = 8


class DataResearchAgent(ModelAgent):
    """Queries the three structured sources for one subtask."""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        toolbelt: Toolbelt,
        collector: SourceCollector,
        records_per_lookup: int = RECORDS_PER_LOOKUP,
        max_output_tokens: int = MAX_RESEARCH_TOKENS,
    ) -> None:
        super().__init__(gateway, role=AgentName.RESEARCHER, max_output_tokens=max_output_tokens)
        self._toolbelt = toolbelt
        self._collector = collector
        self._records = records_per_lookup

    async def research(self, assignment: SubtaskAssignment) -> NodeResult[TaskOutcome]:
        context = AgentContext(
            research_id=assignment.research_id,
            user_id=assignment.user_id,
            mode=assignment.mode,
            iteration=assignment.subtask.iteration,
            task_key=assignment.subtask.key,
        )
        allowed = max(1, min(assignment.query_allowance, len(DataSource)))
        prompt = render(
            "data_queries",
            question=assignment.query,
            subtask=subtask_brief(assignment),
            constraints=constraints(assignment),
            max_queries=str(allowed),
        )
        output, usage = await self.ask(context, prompt=prompt, schema=DataQueries)
        lookups = list(output.lookups[:allowed])

        candidates = await self._look_up(assignment, lookups)
        collected = await self._collector.collect(
            candidates,
            research_id=assignment.research_id,
            task_key=assignment.subtask.key,
            limit=assignment.source_allowance,
        )
        logger.info(
            "data research finished",
            extra={
                "research_id": str(assignment.research_id),
                "task_key": assignment.subtask.key,
                "lookups": [lookup.source.value for lookup in lookups],
                "records": len(candidates),
                "collected": len(collected.sources),
                "uncollectable": collected.failed,
            },
        )
        return NodeResult(
            value=TaskOutcome(
                task_key=assignment.subtask.key,
                iteration=assignment.subtask.iteration,
                sources=collected.sources,
            ),
            # Every lookup is a query against an external index, so each counts
            # against the run's search ceiling exactly as a web search does.
            usage=with_searches(usage, len(lookups)),
        )

    async def _look_up(
        self, assignment: SubtaskAssignment, lookups: list[DataLookup]
    ) -> list[Candidate]:
        outcomes = await asyncio.gather(
            *(self._one(lookup, assignment) for lookup in lookups),
            return_exceptions=True,
        )
        candidates: list[Candidate] = []
        for lookup, outcome in zip(lookups, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                if not isinstance(outcome, Exception):  # pragma: no cover - cancellation
                    raise outcome
                logger.info(
                    "a data lookup failed",
                    extra={
                        "research_id": str(assignment.research_id),
                        "task_key": assignment.subtask.key,
                        "source": lookup.source.value,
                        "error_code": (outcome.code if isinstance(outcome, AppError) else None),
                        "error_type": type(outcome).__name__,
                    },
                )
                continue
            candidates.extend(outcome)
        return candidates

    async def _one(self, lookup: DataLookup, assignment: SubtaskAssignment) -> list[Candidate]:
        match lookup.source:
            case DataSource.SEC:
                sec = await self._toolbelt.search_sec(
                    SearchSecInput(
                        query=lookup.query,
                        forms=lookup.forms,
                        date_from=_iso(assignment.date_range_start),
                        date_to=_iso(assignment.date_range_end),
                        max_results=self._records,
                    )
                )
                return _from_sec(sec.value)
            case DataSource.ARXIV:
                arxiv = await self._toolbelt.search_arxiv(
                    SearchArxivInput(
                        query=lookup.query,
                        category=lookup.category,
                        max_results=self._records,
                    )
                )
                return _from_arxiv(arxiv.value)
            case DataSource.GITHUB:
                github = await self._toolbelt.search_github(
                    SearchGithubInput(query=lookup.query, max_results=self._records)
                )
                return _from_github(github.value)


def _from_sec(output: SearchSecOutput) -> list[Candidate]:
    """Filings as candidates. The publisher is the registrant, per EDGAR."""
    return [
        Candidate(
            url=filing.url,
            title=f"{filing.company_name} {filing.form_type}"
            + (f" filed {filing.filed_at}" if filing.filed_at else ""),
            source_type=SourceType.SEC,
            published_at=_parse_date(filing.filed_at),
            publisher="U.S. Securities and Exchange Commission",
        )
        for filing in output.filings
    ]


def _from_arxiv(output: SearchArxivOutput) -> list[Candidate]:
    """Papers as candidates, citing the abstract page rather than the PDF.

    The abstract page is stable and carries the version history; a PDF URL
    silently changes what it serves when the authors post a revision.
    """
    return [
        Candidate(
            url=paper.url,
            title=paper.title,
            source_type=SourceType.ARXIV,
            published_at=_parse_date(paper.published_at),
            author=", ".join(paper.authors[:5]) or None,
            publisher="arXiv",
        )
        for paper in output.papers
    ]


def _from_github(output: SearchGithubOutput) -> list[Candidate]:
    return [
        Candidate(
            url=repository.url,
            title=repository.full_name,
            source_type=SourceType.GITHUB,
            published_at=_parse_date(repository.pushed_at),
            publisher="GitHub",
        )
        for repository in output.repositories
    ]


def _iso(value: dt.date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_date(value: str | None) -> dt.datetime | None:
    """An API's own timestamp, or ``None``.

    ``None`` where it cannot be read, never today's date: a publication date
    that silently became the date the run happened would make every source look
    current, which is the one thing a reader uses the field for.
    """
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)
