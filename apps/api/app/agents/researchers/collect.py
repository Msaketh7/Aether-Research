"""Fetching a page and making it part of the run's corpus.

Shared by the web and data researchers, because "found a URL" is not a source.
A source is a row with a content hash, an accessed-at and stored bytes, and a
document whose normalised text has offsets that an evidence span can point into.
Until a page has been through here, nothing later in the graph can cite it.

Everything the pipeline already guarantees is reused rather than repeated: the
SSRF guard and the size ceiling are in the fetch tool (Phase 6); parsing happens
in a killable, scrubbed child process and HTML goes through the same readability
extractor a fetched page would (Phase 7); ingestion is idempotent per run and
canonical URL, so two researchers that find the same page collapse onto one
source id and the graph's reducer then counts it once.

**One page's failure is one page.** A fetch that 404s, a PDF that will not
parse, a page whose robots.txt forbids it: each is recorded and the researcher
carries on with the rest. A subtask is lost only when nothing at all could be
collected.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass
from uuid import UUID

from app.agents.schemas import SourceRef
from app.core.enums import SourceType
from app.core.errors import AppError
from app.core.logging import get_logger
from app.retrieval.formats import detect_format
from app.retrieval.ingestion import DocumentIngestor, SourceDescriptor
from app.sources.toolbelt import Toolbelt
from app.sources.tools.fetch import FetchUrlInput
from app.sources.urls import canonicalize

logger = get_logger(__name__)

#: Pages one researcher fetches and ingests at once. Deliberately small: each
#: one holds a document's bytes in memory and a database session while it
#: stores, and several researchers run in parallel already. The graph's
#: concurrency cap multiplies this, not the other way round.
DEFAULT_FETCH_CONCURRENCY = 2

_MAX_TITLE_CHARS = 500


@dataclass(frozen=True, slots=True)
class Candidate:
    """A URL a researcher wants to read, with what its finder said about it.

    ``title`` and ``published_at`` come from a search result or an API record,
    so they are the source's own words about itself. They are stored as data and
    only ever reach a model inside a delimited block.
    """

    url: str
    title: str
    source_type: SourceType
    published_at: dt.datetime | None = None
    author: str | None = None
    publisher: str | None = None


@dataclass(frozen=True, slots=True)
class Collected:
    """What one collection pass produced, including what it could not."""

    sources: tuple[SourceRef, ...]
    failed: int


class SourceCollector:
    """Fetches candidates and ingests them, bounded and per-page fault tolerant."""

    def __init__(
        self,
        *,
        toolbelt: Toolbelt,
        ingestor: DocumentIngestor,
        concurrency: int = DEFAULT_FETCH_CONCURRENCY,
    ) -> None:
        self._toolbelt = toolbelt
        self._ingestor = ingestor
        self._semaphore = asyncio.Semaphore(max(1, concurrency))

    async def collect(
        self,
        candidates: list[Candidate],
        *,
        research_id: UUID,
        task_key: str,
        limit: int,
    ) -> Collected:
        """Fetch and ingest up to ``limit`` candidates, dropping duplicates first.

        Deduplicated by canonical URL before anything is fetched: two search
        results for the same page differing by a tracking parameter would
        otherwise each spend a fetch, and then collapse onto one source anyway.
        """
        unique = _deduplicate(candidates)[:limit]
        if not unique:
            return Collected(sources=(), failed=0)

        results = await asyncio.gather(
            *(
                self._one(candidate, research_id=research_id, task_key=task_key)
                for candidate in unique
            )
        )
        sources = tuple(ref for ref in results if ref is not None)
        return Collected(sources=sources, failed=len(results) - len(sources))

    async def _one(
        self, candidate: Candidate, *, research_id: UUID, task_key: str
    ) -> SourceRef | None:
        async with self._semaphore:
            try:
                return await self._fetch_and_ingest(
                    candidate, research_id=research_id, task_key=task_key
                )
            except Exception as exc:
                # One page, not the subtask. The URL is safe to log - the SSRF
                # guard validated it - but nothing from the body ever is.
                logger.info(
                    "a candidate source could not be collected",
                    extra={
                        "research_id": str(research_id),
                        "task_key": task_key,
                        "url": candidate.url[:200],
                        "error_code": exc.code if isinstance(exc, AppError) else None,
                        "error_type": type(exc).__name__,
                    },
                )
                return None

    async def _fetch_and_ingest(
        self, candidate: Candidate, *, research_id: UUID, task_key: str
    ) -> SourceRef:
        fetched = await self._toolbelt.fetch_url(FetchUrlInput(url=candidate.url))
        page = fetched.value
        detected = detect_format(
            page.raw_bytes, content_type=page.content_type, filename=page.final_url
        )
        accessed_at = dt.datetime.now(dt.UTC)
        outcome = await self._ingestor.ingest(
            page.raw_bytes,
            fmt=detected.format,
            charset=detected.charset,
            descriptor=SourceDescriptor(
                run_id=research_id,
                source_type=candidate.source_type,
                url=page.final_url,
                canonical_url=page.canonical_url,
                domain=page.domain,
                # The registrable domain, not a name from the page. A publisher
                # taken from the document would be the document's claim about
                # who wrote it, which is exactly what a citation must not rest on.
                publisher=candidate.publisher or page.domain,
                accessed_at=accessed_at,
                fallback_title=candidate.title[:_MAX_TITLE_CHARS] or page.domain,
                author=candidate.author,
                published_at=candidate.published_at,
                fetch_metadata={
                    "fetched_status": page.status_code,
                    "redirects": len(page.redirects),
                    "robots_checked": page.robots_checked,
                },
            ),
        )
        logger.debug(
            "source collected",
            extra={
                "research_id": str(research_id),
                "task_key": task_key,
                "source_id": str(outcome.source_id),
                "chunks": outcome.chunk_count,
                "embedded": outcome.embedded,
                "pending": outcome.pending,
                "was_created": outcome.created,
            },
        )
        return SourceRef(
            source_id=outcome.source_id,
            task_key=task_key,
            title=candidate.title[:_MAX_TITLE_CHARS],
            url=page.final_url,
            source_type=candidate.source_type,
            publisher=(candidate.publisher or page.domain)[:300],
            chunk_count=outcome.chunk_count,
        )


def _deduplicate(candidates: list[Candidate]) -> list[Candidate]:
    """One candidate per canonical URL, keeping the first seen.

    A URL this cannot canonicalise is kept as itself rather than dropped: the
    fetch tool validates it properly and refusing it here would hide a working
    source behind a parsing quirk.
    """
    seen: set[str] = set()
    unique: list[Candidate] = []
    for candidate in candidates:
        try:
            key = canonicalize(candidate.url)
        except Exception:
            key = candidate.url
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique
