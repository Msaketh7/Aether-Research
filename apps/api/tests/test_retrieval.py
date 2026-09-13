"""Hybrid retrieval against a real corpus in a real Postgres.

The corpus is ingested by the real pipeline, so the chunks, their offsets, their
metadata and the generated ``tsv`` column are all produced the way production
produces them. Nothing here asserts a hand-built row.

Two halves. The lexical arm, fusion, reranking, filtering and ownership run
everywhere. The dense arm needs the ``embedding`` column, which comes from the
pgvector migration line, so those tests skip where the extension is not
installed and run in CI against the ``pgvector/pgvector`` image - the same split
Phase 7 uses.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from dataclasses import dataclass

import httpx2 as httpx
import pytest
import sqlalchemy as sa

from app.core.config import Settings
from app.core.enums import DocumentFormat, LlmProvider, SourceType
from app.db.models.source import TEXT_SEARCH_CONFIG
from app.models import CollectingCallRecorder, OllamaProvider, build_gateway
from app.retrieval.embedding import ChunkEmbedder, QueryEmbedder
from app.retrieval.factory import build_retriever
from app.retrieval.filters import ChunkFilter
from app.retrieval.query import RetrievalPlan
from app.retrieval.rerank import NoReranker
from app.retrieval.results import RetrievalStrategy
from app.retrieval.retriever import PostgresRetriever
from tests.support import llm
from tests.support.ingestion import descriptor, in_process_ingestor, seed_run
from tests.support.postgres import ProvisionedDatabase

DENSE = RetrievalStrategy.DENSE
LEXICAL = RetrievalStrategy.LEXICAL

#: Four short documents on one subject, written so the queries below have a
#: right answer that is visible in the fixture rather than argued about.
PRICING = (
    "Hosted inference pricing fell sharply during the year. "
    "Providers now quote a price per million tokens rather than per GPU hour, "
    "and the cheapest tier undercuts self-hosting for bursty workloads."
)
HARDWARE = (
    "Memory bandwidth, not raw compute, limits most serving deployments. "
    "Accelerator vendors responded with wider memory interfaces and larger caches, "
    "which helps decoding far more than it helps prefill."
)
FUNDING = (
    "The company raised a Series C of four hundred million dollars. "
    "Investors cited the backlog of enterprise contracts as the reason for the valuation, "
    "which the filing later restated."
)
REGULATION = (
    "Export controls restrict shipments of the fastest accelerators to some regions. "
    "Compliance teams now review every order, and the regulator has signalled further tightening."
)


@dataclass
class Corpus:
    user_id: uuid.UUID
    run_id: uuid.UUID
    documents: dict[str, uuid.UUID]


async def ingest_corpus(database, artifact_store, *, embedder=None) -> Corpus:
    user_id, run_id = await seed_run(database)
    pipeline = in_process_ingestor(database, artifact_store, embedder=embedder)
    documents: dict[str, uuid.UUID] = {}
    for name, body, fmt in (
        ("pricing", PRICING, DocumentFormat.TEXT),
        ("hardware", HARDWARE, DocumentFormat.TEXT),
        ("funding", FUNDING, DocumentFormat.MARKDOWN),
        ("regulation", REGULATION, DocumentFormat.TEXT),
    ):
        outcome = await pipeline.ingest(
            body.encode(),
            fmt=fmt,
            charset=None,
            descriptor=descriptor(run_id, canonical_url=f"upload://{name}"),
        )
        documents[name] = outcome.document_id
    return Corpus(user_id=user_id, run_id=run_id, documents=documents)


@pytest.fixture
async def corpus(database, artifact_store) -> Corpus:
    return await ingest_corpus(database, artifact_store)


def lexical_only(**overrides: object) -> RetrievalPlan:
    """A plan that exercises the half of retrieval that runs without pgvector."""
    return RetrievalPlan(strategy=LEXICAL, **overrides)


def retriever(database, **kwargs: object) -> PostgresRetriever:
    kwargs.setdefault("reranker", NoReranker())
    return PostgresRetriever(database=database, **kwargs)


def documents_of(result, corpus: Corpus) -> list[str]:
    by_id = {document_id: name for name, document_id in corpus.documents.items()}
    return [by_id[hit.chunk.document_id] for hit in result.chunks]


# --- the lexical arm ------------------------------------------------------


async def test_a_keyword_query_finds_the_document_that_uses_the_word(database, corpus):
    result = await retriever(database).retrieve_hybrid(
        "export controls on accelerators",
        filters=ChunkFilter(run_id=corpus.run_id),
        user_id=corpus.user_id,
        plan=lexical_only(),
    )
    assert documents_of(result, corpus)[0] == "regulation"


async def test_the_lexical_arm_stems_so_a_query_need_not_match_the_inflection(database, corpus):
    """`to_tsvector('english')` stems both sides. The fixture says "pricing";
    the query says "priced". A query parsed with a different configuration than
    the column was built with would return nothing here - silently."""
    result = await retriever(database).retrieve_hybrid(
        "how is inference priced",
        filters=ChunkFilter(run_id=corpus.run_id),
        user_id=corpus.user_id,
        plan=lexical_only(),
    )
    assert "pricing" in documents_of(result, corpus)


async def test_punctuation_in_a_query_is_data_not_syntax(database, corpus):
    """`websearch_to_tsquery` is why a query is never escaped. `to_tsquery`
    would raise a syntax error on every one of these."""
    for query in ("memory bandwidth!", "pricing & (", "'unterminated", "a : b"):
        result = await retriever(database).retrieve_hybrid(
            query,
            filters=ChunkFilter(run_id=corpus.run_id),
            user_id=corpus.user_id,
            plan=lexical_only(),
        )
        assert result.arm(LEXICAL) is not None


async def test_a_question_matches_on_some_of_its_terms_not_all_of_them(database, corpus):
    """The defect this phase found. ``websearch_to_tsquery`` - the obvious
    choice - combines unquoted words with AND, so a natural-language question
    requires one chunk to contain every stem in it and matches nothing. No
    chunk here holds all four of these terms; two of them hold two each."""
    result = await retriever(database).retrieve_hybrid(
        "inference pricing memory export",
        filters=ChunkFilter(run_id=corpus.run_id),
        user_id=corpus.user_id,
        plan=lexical_only(),
    )
    assert set(documents_of(result, corpus)) >= {"pricing", "hardware", "regulation"}


async def test_a_query_of_only_stop_words_matches_nothing_without_erroring(database, corpus):
    """It produces no lexemes at all, which must be an empty result rather than
    a syntax error or a match against everything."""
    result = await retriever(database).retrieve_hybrid(
        "the of and is",
        filters=ChunkFilter(run_id=corpus.run_id),
        user_id=corpus.user_id,
        plan=lexical_only(),
    )
    assert len(result) == 0


async def test_the_index_and_the_query_use_the_same_text_search_configuration(database):
    """Read back from the database itself, not from the model.

    A ``tsv`` column generated with one configuration and queried with another
    returns no rows and raises nothing: the two stem differently, so the terms
    never meet. This is the assertion that would catch that.
    """
    async with database.session() as session:
        expression = (
            await session.execute(
                sa.text(
                    "SELECT pg_get_expr(d.adbin, d.adrelid) FROM pg_attrdef d "
                    "JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum "
                    "WHERE d.adrelid = 'document_chunks'::regclass AND a.attname = 'tsv'"
                )
            )
        ).scalar_one()
    assert f"'{TEXT_SEARCH_CONFIG}'" in expression


async def test_a_query_matching_nothing_returns_nothing_rather_than_everything(database, corpus):
    result = await retriever(database).retrieve_hybrid(
        "photosynthesis chlorophyll stomata",
        filters=ChunkFilter(run_id=corpus.run_id),
        user_id=corpus.user_id,
        plan=lexical_only(),
    )
    assert len(result) == 0


async def test_lexical_ranking_is_reproducible(database, corpus):
    """Ties are broken in SQL, so the same query twice is the same ranking."""
    plan = lexical_only()
    first, second = [
        await retriever(database).retrieve_hybrid(
            "inference",
            filters=ChunkFilter(run_id=corpus.run_id),
            user_id=corpus.user_id,
            plan=plan,
        )
        for _ in range(2)
    ]
    assert first.chunk_ids == second.chunk_ids


# --- ownership and filtering ----------------------------------------------


async def test_another_users_corpus_is_invisible(database, artifact_store, corpus, other_user_id):
    """The retriever is the easiest place to leak a corpus: its caller is an
    agent, and agents carry no identity of their own."""
    result = await retriever(database).retrieve_hybrid(
        "export controls",
        filters=ChunkFilter(run_id=corpus.run_id),
        user_id=other_user_id,
        plan=lexical_only(),
    )
    assert len(result) == 0


async def test_another_runs_corpus_is_invisible_to_this_run(database, artifact_store, corpus):
    other = await ingest_corpus(database, artifact_store)
    result = await retriever(database).retrieve_hybrid(
        "export controls",
        filters=ChunkFilter(run_id=corpus.run_id),
        user_id=corpus.user_id,
        plan=lexical_only(),
    )
    assert {hit.chunk.source_id for hit in result.chunks}
    assert other.run_id != corpus.run_id
    assert all(hit.chunk.document_id in corpus.documents.values() for hit in result.chunks)


async def test_a_filter_narrows_the_search_as_well_as_the_listing(database, corpus):
    """Both arms go through one filter builder, so a filter cannot be honoured
    by the listing path and ignored by the search path."""
    result = await retriever(database).retrieve_hybrid(
        "inference pricing memory export",
        filters=ChunkFilter(run_id=corpus.run_id, document_ids=(corpus.documents["hardware"],)),
        user_id=corpus.user_id,
        plan=lexical_only(),
    )
    assert set(documents_of(result, corpus)) == {"hardware"}


async def test_a_format_filter_reaches_the_search_path(database, corpus):
    result = await retriever(database).retrieve_hybrid(
        "series c valuation backlog",
        filters=ChunkFilter(run_id=corpus.run_id, formats=(DocumentFormat.PDF,)),
        user_id=corpus.user_id,
        plan=lexical_only(),
    )
    assert len(result) == 0


async def test_a_source_type_filter_reaches_the_search_path(database, corpus):
    hits = await retriever(database).retrieve_hybrid(
        "export controls",
        filters=ChunkFilter(run_id=corpus.run_id, source_types=(SourceType.UPLOAD,)),
        user_id=corpus.user_id,
        plan=lexical_only(),
    )
    misses = await retriever(database).retrieve_hybrid(
        "export controls",
        filters=ChunkFilter(run_id=corpus.run_id, source_types=(SourceType.SEC,)),
        user_id=corpus.user_id,
        plan=lexical_only(),
    )
    assert len(hits) > 0
    assert len(misses) == 0


# --- the interface --------------------------------------------------------


async def test_retrieve_uses_the_configured_default_plan(database, corpus):
    plan = lexical_only(limit=2, candidates=20)
    result = await retriever(database, default_plan=plan).retrieve(
        "inference", run_id=corpus.run_id, user_id=corpus.user_id
    )
    assert len(result) <= 2
    assert result.strategy is LEXICAL


async def test_an_explicit_limit_raises_the_candidate_count_to_match(database, corpus):
    """Otherwise a caller asking for more than the plan fetches would get a plan
    that cannot satisfy its own limit - and a short result with no explanation."""
    plan = lexical_only(limit=2, candidates=2)
    result = await retriever(database, default_plan=plan).retrieve_with_filters(
        "inference",
        filters=ChunkFilter(run_id=corpus.run_id),
        user_id=corpus.user_id,
        limit=8,
    )
    assert result.candidates >= len(result.chunks)


async def test_an_empty_query_is_refused_before_it_reaches_the_database(database, corpus):
    """An unvalidated empty query would parse to an empty tsquery and match
    nothing, which looks like a corpus problem rather than a caller bug."""
    with pytest.raises(ValueError):
        await retriever(database).retrieve("   ", run_id=corpus.run_id, user_id=corpus.user_id)


async def test_every_result_carries_the_arm_that_found_it(database, corpus):
    result = await retriever(database).retrieve_hybrid(
        "export controls",
        filters=ChunkFilter(run_id=corpus.run_id),
        user_id=corpus.user_id,
        plan=lexical_only(),
    )
    assert all(hit.found_by == (LEXICAL,) for hit in result.chunks)
    assert all(hit.lexical_rank is not None for hit in result.chunks)


async def test_retrieved_text_is_still_untrusted(database, corpus):
    """A chunk read back from the index is retrieved source content like any
    other (ADR 0011). The type is what stops it being read as instructions."""
    result = await retriever(database).retrieve_hybrid(
        "export controls",
        filters=ChunkFilter(run_id=corpus.run_id),
        user_id=corpus.user_id,
        plan=lexical_only(),
    )
    assert "UNTRUSTED" in result.chunks[0].chunk.text.for_prompt()


# --- honest emptiness -----------------------------------------------------


async def test_a_hybrid_plan_without_an_embedding_model_says_the_dense_arm_was_skipped(
    database, corpus
):
    """The distinction the engineering rules require: not built, not zero."""
    result = await retriever(database).retrieve_hybrid(
        "export controls",
        filters=ChunkFilter(run_id=corpus.run_id),
        user_id=corpus.user_id,
        plan=RetrievalPlan(),
    )
    dense = result.arm(DENSE)
    assert dense is not None and dense.skipped
    assert "No embedding model" in (dense.skipped_reason or "")
    assert len(result) > 0


async def test_a_zero_weight_arm_reports_itself_skipped_rather_than_empty(database, corpus):
    result = await retriever(database).retrieve_hybrid(
        "export controls",
        filters=ChunkFilter(run_id=corpus.run_id),
        user_id=corpus.user_id,
        plan=RetrievalPlan(dense_weight=0.0),
    )
    dense = result.arm(DENSE)
    assert dense is not None and dense.skipped_reason == "This arm's fusion weight is zero."


async def test_a_lexical_plan_reports_the_dense_arm_as_not_asked_for(database, corpus):
    result = await retriever(database).retrieve_hybrid(
        "export controls",
        filters=ChunkFilter(run_id=corpus.run_id),
        user_id=corpus.user_id,
        plan=lexical_only(),
    )
    dense = result.arm(DENSE)
    assert dense is not None and dense.skipped_reason == "This plan is lexical only."


# --- the dense arm (pgvector) ---------------------------------------------


@pytest.fixture
def pgvector(postgres: ProvisionedDatabase | None) -> None:
    if postgres is None or not postgres.has_pgvector:
        pytest.skip(
            "pgvector is not installed on this server. The embedding column comes from the "
            "vector migration line; CI runs these against the pgvector/pgvector image."
        )


#: A deterministic stand-in for an embedding model: each text is mapped to a
#: unit vector by hashing its words into buckets. It is not semantic and makes
#: no claim to be - it exists so the *SQL* can be exercised. Quality numbers
#: come from the benchmark with a real model, never from this.
def bag_of_words_vector(text: str, dimensions: int = 768) -> list[float]:
    vector = [0.0] * dimensions
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        # blake2b, not the built-in hash: string hashing is salted per process,
        # so a vector written by one run would not match a query in the next.
        bucket = int.from_bytes(hashlib.blake2b(word.encode(), digest_size=4).digest(), "big")
        vector[bucket % dimensions] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def embedding_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    return llm.ollama_embeddings([bag_of_words_vector(text) for text in body["input"]])


def chunk_embedder() -> ChunkEmbedder:
    provider = OllamaProvider(
        base_url="http://ollama.test",
        timeout_seconds=5,
        transport=llm.transport(embedding_handler),
    )
    gateway = build_gateway(
        Settings(app_env="test", llm_max_attempts=1),
        providers={LlmProvider.OLLAMA: provider},
        recorder=CollectingCallRecorder(),
    )
    return ChunkEmbedder(gateway, batch_size=8)


async def test_the_dense_arm_finds_the_document_that_shares_the_query_terms(
    pgvector, database, artifact_store
):
    """The vector SQL end to end: the double cast, the model filter, the HNSW
    ordering. The embedder is deterministic rather than semantic, so what is
    asserted is that nearest-neighbour search works - not that it is clever."""
    embedder = chunk_embedder()
    corpus = await ingest_corpus(database, artifact_store, embedder=embedder)
    result = await retriever(database, query_embedder=QueryEmbedder(embedder)).retrieve_hybrid(
        "memory bandwidth limits serving deployments",
        filters=ChunkFilter(run_id=corpus.run_id),
        user_id=corpus.user_id,
        plan=RetrievalPlan(strategy=DENSE),
    )
    assert documents_of(result, corpus)[0] == "hardware"
    dense = result.arm(DENSE)
    assert dense is not None and dense.ran


async def test_the_dense_arm_ignores_vectors_from_another_model(pgvector, database, artifact_store):
    """Two models' vectors share a column but not a space. A retriever that
    mixed them would rank by nothing, and the failure would be invisible."""
    embedder = chunk_embedder()
    corpus = await ingest_corpus(database, artifact_store, embedder=embedder)

    class OtherModel(QueryEmbedder):
        @property
        def model_label(self) -> str:
            return "openai/text-embedding-3-small"

    result = await retriever(database, query_embedder=OtherModel(embedder)).retrieve_hybrid(
        "memory bandwidth",
        filters=ChunkFilter(run_id=corpus.run_id),
        user_id=corpus.user_id,
        plan=RetrievalPlan(strategy=DENSE),
    )
    assert len(result) == 0


async def test_the_dense_arm_honours_a_filter(pgvector, database, artifact_store):
    embedder = chunk_embedder()
    corpus = await ingest_corpus(database, artifact_store, embedder=embedder)
    result = await retriever(database, query_embedder=QueryEmbedder(embedder)).retrieve_hybrid(
        "memory bandwidth",
        filters=ChunkFilter(run_id=corpus.run_id, document_ids=(corpus.documents["funding"],)),
        user_id=corpus.user_id,
        plan=RetrievalPlan(strategy=DENSE),
    )
    assert set(documents_of(result, corpus)) == {"funding"}


async def test_a_chunk_both_arms_find_outranks_one_only_a_single_arm_found(
    pgvector, database, artifact_store
):
    """What hybrid retrieval is for, asserted end to end rather than in fusion."""
    embedder = chunk_embedder()
    corpus = await ingest_corpus(database, artifact_store, embedder=embedder)
    result = await retriever(database, query_embedder=QueryEmbedder(embedder)).retrieve_hybrid(
        "memory bandwidth limits serving deployments",
        filters=ChunkFilter(run_id=corpus.run_id),
        user_id=corpus.user_id,
        plan=RetrievalPlan(),
    )
    assert result.chunks[0].found_by == (DENSE, LEXICAL)
    assert all(arm.ran for arm in result.arms)


async def test_an_unembedded_corpus_gives_a_dense_arm_that_ran_and_found_nothing(
    pgvector, database, corpus
):
    """Distinct from a skipped arm: the model is configured, the search ran, and
    there is simply nothing indexed yet."""
    result = await retriever(
        database, query_embedder=QueryEmbedder(chunk_embedder())
    ).retrieve_hybrid(
        "memory bandwidth",
        filters=ChunkFilter(run_id=corpus.run_id),
        user_id=corpus.user_id,
        plan=RetrievalPlan(),
    )
    dense = result.arm(DENSE)
    assert dense is not None and dense.ran and dense.returned == 0
    assert len(result) > 0


async def test_a_limit_past_the_plans_ceiling_is_refused_rather_than_clamped(database, corpus):
    """The plan declares a ceiling on how many chunks a call returns. Copying the
    plan without re-validating would accept a larger one here and clamp it in
    SQL - a declared bound that silently does not hold."""
    with pytest.raises(ValueError):
        await retriever(database).retrieve(
            "inference", run_id=corpus.run_id, user_id=corpus.user_id, limit=10_000
        )


async def test_the_factory_builds_a_working_lexical_only_retriever(settings, database, corpus):
    """The deployment `build_document_ingestor` already supports - no embedding
    model configured - wired end to end. It searches; it does not pretend to
    have searched by meaning."""
    built = build_retriever(settings, database=database, gateway=None)

    result = await built.retrieve("export controls", run_id=corpus.run_id, user_id=corpus.user_id)

    assert len(result) > 0
    assert result.reranker == f"mmr(lambda={settings.retrieval_mmr_lambda:g})"
    dense = next(arm for arm in result.arms if arm.strategy is DENSE)
    assert dense.skipped and "No embedding model" in (dense.skipped_reason or "")
