"""Metadata filtering: narrowing a run's chunks before anything ranks them.

A small corpus in two runs owned by two users - a three-page PDF, a sectioned
Markdown file, a French text with a publication date, and one document in the
other user's run - and each filter asserted against what it should and should
not return.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from app.core.enums import DocumentFormat, SourceType
from app.db.repositories.documents import SqlAlchemyDocumentRepository
from app.retrieval.filters import ChunkFilter, ChunkView
from app.retrieval.ingestion import IngestionOutcome
from tests.support.documents import make_pdf, prose
from tests.support.ingestion import descriptor, in_process_ingestor, seed_run

FRENCH = (
    "Le chiffre d'affaires de l'entreprise a fortement augmenté au cours du trimestre, "
    "selon les résultats publiés mardi. Les analystes estiment que la demande restera "
    "soutenue pendant toute l'année, malgré les contraintes d'approvisionnement. "
)


@dataclass
class Corpus:
    user_a: uuid.UUID
    run_a: uuid.UUID
    user_b: uuid.UUID
    run_b: uuid.UUID
    pdf: IngestionOutcome
    markdown: IngestionOutcome
    french: IngestionOutcome
    other: IngestionOutcome


@pytest.fixture
async def corpus(database, artifact_store) -> Corpus:
    user_a, run_a = await seed_run(database)
    user_b, run_b = await seed_run(database)
    pipeline = in_process_ingestor(database, artifact_store)

    pdf = await pipeline.ingest(
        make_pdf([prose(2), prose(2, offset=3), prose(2, offset=6)]),
        fmt=DocumentFormat.PDF,
        charset=None,
        descriptor=descriptor(run_a, canonical_url="upload://pdf"),
    )
    markdown = await pipeline.ingest(
        ("# Guide\n\n" + prose(2) + "\n\n## 50% off\n\n" + prose(2, offset=4)).encode(),
        fmt=DocumentFormat.MARKDOWN,
        charset=None,
        descriptor=descriptor(run_a, canonical_url="upload://markdown"),
    )
    french = await pipeline.ingest(
        (FRENCH * 3).encode(),
        fmt=DocumentFormat.TEXT,
        charset=None,
        descriptor=descriptor(
            run_a,
            canonical_url="upload://french",
            published_at=dt.datetime(2024, 1, 15, tzinfo=dt.UTC),
        ),
    )
    other = await pipeline.ingest(
        prose(2).encode(),
        fmt=DocumentFormat.TEXT,
        charset=None,
        descriptor=descriptor(run_b, canonical_url="upload://other"),
    )
    return Corpus(user_a, run_a, user_b, run_b, pdf, markdown, french, other)


async def chunks(
    database,
    corpus: Corpus,
    *,
    user_id: uuid.UUID | None = None,
    run_id: uuid.UUID | None = None,
    **filters: object,
) -> list[ChunkView]:
    query = ChunkFilter(run_id=run_id or corpus.run_a, **filters)  # type: ignore[arg-type]
    async with database.session() as session:
        views, has_more = await SqlAlchemyDocumentRepository(session).list_chunks(
            query, user_id=user_id or corpus.user_a, limit=200
        )
    assert has_more is False
    return views


def documents(views: list[ChunkView]) -> set[uuid.UUID]:
    return {view.document_id for view in views}


async def test_a_runs_chunks_are_invisible_to_another_user(database, corpus):
    assert await chunks(database, corpus, user_id=corpus.user_b) == []


async def test_without_filters_a_run_returns_its_own_chunks_only(database, corpus):
    views = await chunks(database, corpus)
    assert documents(views) == {
        corpus.pdf.document_id,
        corpus.markdown.document_id,
        corpus.french.document_id,
    }
    assert len(views) == (
        corpus.pdf.chunk_count + corpus.markdown.chunk_count + corpus.french.chunk_count
    )


async def test_format_filters_read_the_chunk_metadata(database, corpus):
    assert documents(await chunks(database, corpus, formats=(DocumentFormat.PDF,))) == {
        corpus.pdf.document_id
    }
    assert documents(
        await chunks(database, corpus, formats=(DocumentFormat.MARKDOWN, DocumentFormat.TEXT))
    ) == {corpus.markdown.document_id, corpus.french.document_id}


async def test_a_page_range_returns_the_chunks_that_touch_it_and_nothing_pageless(database, corpus):
    views = await chunks(database, corpus, page_from=2, page_to=2)
    assert views
    assert documents(views) == {corpus.pdf.document_id}
    for view in views:
        assert view.page_start is not None
        assert view.page_end is not None
        assert view.page_start <= 2 <= view.page_end


async def test_a_section_prefix_matches_literally(database, corpus):
    """``%`` in a prefix is a character, not a LIKE wildcard."""
    guide = await chunks(database, corpus, section_prefix="Guide")
    assert documents(guide) == {corpus.markdown.document_id}

    sale = await chunks(database, corpus, section_prefix="Guide > 50%")
    assert sale
    assert {view.section for view in sale} == {"Guide > 50% off"}

    assert await chunks(database, corpus, section_prefix="%") == []


async def test_language_filter_uses_the_detected_language(database, corpus):
    assert documents(await chunks(database, corpus, languages=("fr",))) == {
        corpus.french.document_id
    }


async def test_publication_date_filters_exclude_sources_without_a_date(database, corpus):
    after = await chunks(database, corpus, published_after=dt.datetime(2024, 1, 1, tzinfo=dt.UTC))
    assert documents(after) == {corpus.french.document_id}
    assert (
        await chunks(database, corpus, published_before=dt.datetime(2023, 12, 31, tzinfo=dt.UTC))
        == []
    )


async def test_the_embedded_filter_tells_waiting_chunks_from_searchable_ones(database, corpus):
    assert await chunks(database, corpus, embedded=True) == []
    assert len(await chunks(database, corpus, embedded=False)) == len(
        await chunks(database, corpus)
    )


async def test_source_type_and_id_filters(database, corpus):
    assert await chunks(database, corpus, source_types=(SourceType.WEB,)) == []
    assert documents(
        await chunks(database, corpus, document_ids=(corpus.markdown.document_id,))
    ) == {corpus.markdown.document_id}
    assert documents(await chunks(database, corpus, source_ids=(corpus.pdf.source_id,))) == {
        corpus.pdf.document_id
    }


async def test_keyset_pages_cover_every_chunk_exactly_once(database, corpus):
    everything = await chunks(database, corpus)
    seen: list[uuid.UUID] = []
    after = None
    async with database.session() as session:
        repository = SqlAlchemyDocumentRepository(session)
        for _ in range(100):
            page, has_more = await repository.list_chunks(
                ChunkFilter(run_id=corpus.run_a), user_id=corpus.user_a, limit=3, after=after
            )
            seen += [view.id for view in page]
            if not has_more:
                break
            after = (page[-1].document_id, page[-1].chunk_index)
    assert seen == [view.id for view in everything]


async def test_retrieved_text_cannot_be_interpolated_into_a_prompt(database, corpus):
    """A chunk read back from the index is retrieved content like any other (ADR 0011)."""
    view = (await chunks(database, corpus))[0]
    with pytest.raises(TypeError):
        str(view.text)
    assert view.text.expose()


def test_a_filter_is_validated_before_it_reaches_sql():
    run_id = uuid.uuid4()
    with pytest.raises(ValidationError):
        ChunkFilter(run_id=run_id, page_from=5, page_to=2)
    with pytest.raises(ValidationError):
        ChunkFilter(run_id=run_id, languages=("english",))
    with pytest.raises(ValidationError):
        ChunkFilter(run_id=run_id, colour="blue")  # type: ignore[call-arg]
