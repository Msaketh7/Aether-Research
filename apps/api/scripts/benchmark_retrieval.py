"""Measure the retrieval strategies against a labelled corpus.

    uv run python scripts/with_test_db.py uv run python scripts/benchmark_retrieval.py

That form provisions a throwaway Postgres, ingests the corpus through the real
pipeline, labels the chunks, and runs every strategy over every case. Point
``DATABASE_URL`` at an existing database to skip the provisioning.

Two questions this answers, both of which the build plan asks for and neither of
which had a number before:

* **Which strategy is best here** - dense, lexical, hybrid, hybrid + rerank.
* **Whether 512/64 is the right chunk size**, by ingesting the same corpus at
  several sizes and scoring each. ADR 0012 recorded 512/64 as a documented
  default explicitly pending this.

What it will not do is invent a number. A strategy whose arm cannot run here -
no embedding model configured, no pgvector - is reported as *not measured*, with
the reason, and never as a strategy that scored badly.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
import uuid
from pathlib import Path

import sqlalchemy as sa

from app.core.config import Settings
from app.core.enums import DocumentFormat, SourceType
from app.db.models.research import ResearchRunRow
from app.db.repositories.documents import SqlAlchemyDocumentRepository
from app.db.repositories.user import UserRepository
from app.db.session import Database
from app.models import build_gateway, build_providers
from app.retrieval.benchmark import (
    BenchmarkReport,
    CorpusProfile,
    collect_chunks,
    label,
    load_cases,
    run_benchmark,
)
from app.retrieval.chunking import Chunker
from app.retrieval.embedding import ChunkEmbedder, QueryEmbedder
from app.retrieval.factory import build_reranker, parse_limits
from app.retrieval.ingestion import DocumentIngestor, SourceDescriptor
from app.retrieval.isolation import IsolatedParser
from app.retrieval.query import RetrievalPlan
from app.retrieval.results import RetrievalStrategy
from app.retrieval.retriever import PostgresRetriever
from app.storage import build_object_storage

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATASET = REPO_ROOT / "data" / "eval" / "retrieval" / "repo-docs-v1.json"

#: Sizes swept when ``--chunk-sizes`` is not given. 512 is the current default;
#: the neighbours are one halving and one doubling, which is enough to show a
#: trend without pretending to have searched the space.
DEFAULT_SIZES = (256, 512, 1024)

#: Overlap is held at an eighth of the chunk, the ratio 512/64 expresses, so the
#: sweep varies one thing.
OVERLAP_RATIO = 8


def strategies(limit: int, candidates: int, mmr_lambda: float) -> dict[str, RetrievalPlan]:
    """The four arms the evaluation methodology names, as plans.

    "hybrid" and "hybrid+rerank" differ only in the reranker, so the comparison
    isolates it; the retriever is built with the reranker and the plan switches
    it off, rather than the two being different objects.
    """
    common = {"limit": limit, "candidates": candidates}
    return {
        "dense": RetrievalPlan(strategy=RetrievalStrategy.DENSE, rerank=False, **common),
        "lexical": RetrievalPlan(strategy=RetrievalStrategy.LEXICAL, rerank=False, **common),
        "hybrid": RetrievalPlan(strategy=RetrievalStrategy.HYBRID, rerank=False, **common),
        f"hybrid+mmr({mmr_lambda:g})": RetrievalPlan(
            strategy=RetrievalStrategy.HYBRID, rerank=True, **common
        ),
    }


def format_of(path: Path) -> DocumentFormat:
    return {
        ".md": DocumentFormat.MARKDOWN,
        ".markdown": DocumentFormat.MARKDOWN,
        ".pdf": DocumentFormat.PDF,
        ".html": DocumentFormat.HTML,
        ".htm": DocumentFormat.HTML,
    }.get(path.suffix.lower(), DocumentFormat.TEXT)


async def seed_run(database: Database, user_id: uuid.UUID, title: str) -> uuid.UUID:
    async with database.session() as session:
        await UserRepository(session).ensure(user_id, f"{user_id}@benchmark.invalid")
        run = ResearchRunRow(
            user_id=user_id,
            title=title,
            question=title,
            mode="deep",
            status="queued",
        )
        session.add(run)
        await session.flush()
        return run.id


async def ingest(
    ingestor: DocumentIngestor, run_id: uuid.UUID, files: list[Path]
) -> tuple[int, int, int]:
    """Ingest the corpus into a run. Returns (documents, chunks, embedded)."""
    documents = chunks = embedded = 0
    for path in files:
        outcome = await ingestor.ingest(
            path.read_bytes(),
            fmt=format_of(path),
            charset=None,
            descriptor=SourceDescriptor(
                run_id=run_id,
                source_type=SourceType.UPLOAD,
                url=f"file://{path.as_posix()}",
                canonical_url=f"file://{path.as_posix()}",
                domain="benchmark-corpus",
                publisher="Aether Research repository",
                accessed_at=_now(),
                fallback_title=path.name,
            ),
        )
        documents += 1
        chunks += outcome.chunk_count
        embedded += outcome.embedded
    return documents, chunks, embedded


def _now() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


async def measure(
    *,
    database: Database,
    settings: Settings,
    files: list[Path],
    dataset: Path,
    chunk_size: int,
    limit: int,
    candidates: int,
    user_id: uuid.UUID,
    embedder: ChunkEmbedder | None,
) -> BenchmarkReport:
    overlap = chunk_size // OVERLAP_RATIO
    run_id = await seed_run(database, user_id, f"retrieval benchmark, {chunk_size}/{overlap}")
    ingestor = DocumentIngestor(
        database=database,
        storage=build_object_storage(settings),
        parser=IsolatedParser(
            limits=parse_limits(settings),
            timeout_seconds=settings.parse_timeout_seconds,
            max_memory_bytes=settings.parse_max_memory_bytes,
            max_concurrent=settings.max_concurrent_parses,
        ),
        chunker=Chunker(
            chunk_size_tokens=chunk_size,
            chunk_overlap_tokens=overlap,
            max_chunks=settings.max_chunks_per_document,
        ),
        embedder=embedder,
        max_chunks=settings.max_chunks_per_document,
    )
    documents, chunk_count, embedded = await ingest(ingestor, run_id, files)

    async with database.session() as session:
        chunks = await collect_chunks(
            SqlAlchemyDocumentRepository(session), run_id=run_id, user_id=user_id
        )
    labelled = label(load_cases(dataset), chunks)
    for item in labelled:
        if item.missing_anchors:
            print(
                f"  ! {item.case.id}: {len(item.missing_anchors)} anchor(s) matched no chunk",
                file=sys.stderr,
            )

    retriever = PostgresRetriever(
        database=database,
        query_embedder=None if embedder is None else QueryEmbedder(embedder),
        reranker=build_reranker(settings),
    )
    return await run_benchmark(
        retriever,
        labelled=labelled,
        corpus=CorpusProfile(
            run_id=run_id,
            documents=documents,
            chunks=chunk_count,
            embedded_chunks=embedded,
            chunk_size_tokens=chunk_size,
            chunk_overlap_tokens=overlap,
        ),
        plans=strategies(limit, candidates, settings.retrieval_mmr_lambda),
        user_id=user_id,
    )


def build_embedder(settings: Settings, *, wanted: bool) -> ChunkEmbedder | None:
    """The embedder, or ``None`` with the reason printed.

    Refusing quietly would turn "no model configured" into "dense retrieval
    scored zero", which is the one thing this script must never report.
    """
    if not wanted:
        print("dense arms: not measured (pass --embed to use the configured model)")
        return None
    providers = build_providers(settings)
    if not providers:
        print("dense arms: not measured (no model provider is configured)")
        return None
    gateway = build_gateway(settings, providers=providers)
    try:
        return ChunkEmbedder(gateway, batch_size=settings.embedding_batch_size)
    except Exception as exc:  # the reason is the useful part, whatever it is
        print(f"dense arms: not measured ({exc})")
        return None


def migrate(database_url: str, *, with_vector: bool) -> None:
    """Bring the schema up, the same way the test suite does.

    The script is normally pointed at a throwaway cluster, which has no schema
    at all, so migrating here is what makes it one command. The target follows
    the same rule as the suite: both branches where pgvector exists, the
    relational one where it does not.
    """
    from alembic import command
    from alembic.config import Config

    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "heads" if with_vector else "core@head")


async def has_pgvector(database: Database) -> bool:
    """Whether the extension is installed. Answerable before the schema exists."""
    async with database.session() as session:
        return bool(
            (
                await session.execute(
                    sa.text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
                )
            ).scalar_one()
        )


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--corpus",
        type=Path,
        nargs="*",
        help="Files to ingest. Defaults to the documents the dataset names.",
    )
    parser.add_argument("--chunk-sizes", type=int, nargs="*", default=list(DEFAULT_SIZES))
    parser.add_argument("--limit", type=int, default=10, help="k, for recall@k and the rest.")
    parser.add_argument("--candidates", type=int, default=50)
    parser.add_argument(
        "--embed",
        action="store_true",
        help="Embed the corpus and measure the dense arms. Needs a model and pgvector.",
    )
    parser.add_argument(
        "--no-migrate",
        action="store_true",
        help="Assume the schema is already there. The default migrates it.",
    )
    args = parser.parse_args(argv)

    manifest = json.loads(args.dataset.read_text(encoding="utf-8"))
    files = args.corpus or [REPO_ROOT / name for name in manifest.get("corpus", [])]
    missing = [path for path in files if not path.is_file()]
    if missing:
        print(f"corpus files not found: {missing}", file=sys.stderr)
        return 2

    settings = Settings(app_env="test")
    database = Database(settings)
    user_id = uuid.uuid4()
    try:
        with_vector = await has_pgvector(database)
        if not args.no_migrate:
            # In a worker thread: Alembic's env.py calls asyncio.run itself, and
            # it cannot do that from inside this one's running loop.
            await asyncio.to_thread(migrate, settings.database_url, with_vector=with_vector)

        embedder = build_embedder(settings, wanted=args.embed)
        if embedder is not None and not with_vector:
            print("dense arms: not measured (pgvector is not installed on this server)")
            embedder = None

        print(f"dataset: {args.dataset.name}, {len(manifest['cases'])} cases")
        print(f"corpus:  {len(files)} files\n")
        for chunk_size in args.chunk_sizes:
            report = await measure(
                database=database,
                settings=settings,
                files=list(files),
                dataset=args.dataset,
                chunk_size=chunk_size,
                limit=args.limit,
                candidates=args.candidates,
                user_id=user_id,
                embedder=embedder,
            )
            print(report.render())
            print()
    finally:
        await database.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
