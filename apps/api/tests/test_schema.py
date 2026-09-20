"""The schema itself.

These run against the database the Alembic migrations actually produced, not
against ``Base.metadata.create_all``. The distinction matters: the migrations
are what build the schema everywhere else, so testing the models against
themselves would prove nothing about the thing that ships.
"""

from __future__ import annotations

import datetime as dt
import uuid

import asyncpg
import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.base import Base
from app.db.external import EXTERNALLY_OWNED_TABLES
from app.db.models.source import EMBEDDING_DIMENSIONS
from app.db.models.user import UserRow
from tests.support.postgres import ProvisionedDatabase

EXPECTED_TABLES = {
    "agent_runs",
    "citations",
    "claims",
    "contradictions",
    "document_chunks",
    "documents",
    "evaluations",
    "evidence",
    "feedback",
    "llm_calls",
    "report_sections",
    "reports",
    "research_events",
    "research_projects",
    "research_run_uploads",
    "research_runs",
    "research_tasks",
    "sessions",
    "sources",
    "tool_calls",
    "uploads",
    "users",
}


@pytest.fixture
async def engine(postgres: ProvisionedDatabase | None) -> AsyncEngine:
    if postgres is None:
        pytest.skip("no Postgres available")
    engine = create_async_engine(postgres.url, poolclass=sa.pool.NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def raw(postgres: ProvisionedDatabase | None) -> asyncpg.Connection:
    """A bare connection, for asserting on constraint violations directly."""
    if postgres is None:
        pytest.skip("no Postgres available")
    connection = await asyncpg.connect(postgres.url.replace("+asyncpg", ""))
    try:
        yield connection
    finally:
        await connection.close()


async def seed_user(raw: asyncpg.Connection, email: str = "a@example.com") -> uuid.UUID:
    return await raw.fetchval(
        "INSERT INTO users (email, name) VALUES ($1, 'A') RETURNING id", email
    )


async def seed_run(raw: asyncpg.Connection, user_id: uuid.UUID, **overrides: object) -> uuid.UUID:
    values: dict[str, object] = {
        "title": "t",
        "question": "q",
        "mode": "deep",
        "depth": 3,
        "status": "queued",
    }
    values.update(overrides)
    columns = ", ".join(["user_id", *values])
    placeholders = ", ".join(f"${i}" for i in range(1, len(values) + 2))
    return await raw.fetchval(
        # Column names come from this function's own literals, never from input.
        f"INSERT INTO research_runs ({columns}) VALUES ({placeholders}) RETURNING id",  # noqa: S608
        user_id,
        *values.values(),
    )


# --- structure ------------------------------------------------------------


async def test_every_table_from_the_design_exists(engine: AsyncEngine):
    async with engine.connect() as connection:
        names = await connection.run_sync(lambda sync: set(sa.inspect(sync).get_table_names()))

    assert names >= EXPECTED_TABLES, f"missing: {sorted(EXPECTED_TABLES - names)}"


def _modelled_only(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    """The drift check covers what the ORM models.

    The checkpoint tables are LangGraph's: Alembic creates them, no model
    describes them, and ``app.db.external`` names them (ADR 0014).
    """
    if type_ == "table":
        return name not in EXTERNALLY_OWNED_TABLES
    return parent_names.get("table_name") not in EXTERNALLY_OWNED_TABLES


# Alembic notes that it cannot emit an ALTER for a generated column's default.
# That is information about what autogenerate could do, not a schema problem:
# `tsv` is Computed and correct in both the model and the database.
@pytest.mark.filterwarnings(
    "ignore:Computed default on document_chunks.tsv cannot be modified:UserWarning"
)
async def test_the_migrations_and_the_models_agree(
    engine: AsyncEngine, postgres: ProvisionedDatabase
):
    """No drift between what Alembic built and what the ORM expects.

    This is the test that catches the most common and most expensive schema
    mistake: editing a model and forgetting the migration. It fails the build
    rather than surprising the first deployment.
    """

    def diff(sync_connection: sa.Connection) -> list[object]:
        context = MigrationContext.configure(
            sync_connection,
            opts={
                "compare_type": True,
                "compare_server_default": True,
                "include_name": _modelled_only,
            },
        )
        return list(compare_metadata(context, Base.metadata))

    async with engine.connect() as connection:
        differences = await connection.run_sync(diff)

    if not postgres.has_pgvector:
        # Without the extension the suite migrates only the relational line, so
        # the embedding column is legitimately absent. Asserting that it is the *only* difference is
        # stronger than skipping: real drift anywhere else still fails here.
        expected = [("add_column", None, "document_chunks")]
        actual = [
            (entry[0], entry[1], entry[2])
            for entry in differences
            if isinstance(entry, tuple) and entry[0] == "add_column"
        ]
        assert actual == expected, f"schema drift: {differences}"
        assert len(differences) == 1, f"schema drift: {differences}"
        assert differences[0][3].name == "embedding"  # type: ignore[index]
        return

    assert differences == [], f"schema drift: {differences}"


async def test_every_foreign_key_is_indexed(engine: AsyncEngine):
    """An unindexed foreign key makes every cascade and join a sequential scan."""

    def collect(sync_connection: sa.Connection) -> list[str]:
        inspector = sa.inspect(sync_connection)
        problems: list[str] = []
        for table in inspector.get_table_names():
            indexed = {
                next(iter(index["column_names"]))
                for index in inspector.get_indexes(table)
                if index["column_names"]
            }
            primary_key = inspector.get_pk_constraint(table)["constrained_columns"] or []
            indexed |= set(primary_key[:1])
            for key in inspector.get_foreign_keys(table):
                column = key["constrained_columns"][0]
                if column not in indexed:
                    problems.append(f"{table}.{column}")
        return problems

    async with engine.connect() as connection:
        problems = await connection.run_sync(collect)

    assert problems == [], f"unindexed foreign keys: {problems}"


# --- constraints ----------------------------------------------------------


async def test_email_uniqueness_ignores_case(raw: asyncpg.Connection):
    """citext, so one person cannot register twice with different casing."""
    await seed_user(raw, "Ada@Example.com")

    with pytest.raises(asyncpg.UniqueViolationError):
        await seed_user(raw, "ada@example.com")


async def test_an_out_of_range_depth_is_rejected_by_the_database(raw: asyncpg.Connection):
    """Validation exists in the API too; the database is the backstop that also
    covers a migration, a backfill or a hand-written UPDATE."""
    user_id = await seed_user(raw)

    with pytest.raises(asyncpg.CheckViolationError):
        await seed_run(raw, user_id, depth=9)


async def test_an_unknown_status_is_rejected(raw: asyncpg.Connection):
    user_id = await seed_user(raw)

    with pytest.raises(asyncpg.CheckViolationError):
        await seed_run(raw, user_id, status="thinking-really-hard")


async def test_progress_outside_zero_to_one_is_rejected(raw: asyncpg.Connection):
    user_id = await seed_user(raw)

    with pytest.raises(asyncpg.CheckViolationError):
        await seed_run(raw, user_id, progress=1.5)


async def test_an_evidence_span_must_be_ordered(raw: asyncpg.Connection):
    """A span whose end precedes its start cannot be re-read from the document."""
    user_id = await seed_user(raw)
    run_id = await seed_run(raw, user_id)
    source_id = await raw.fetchval(
        """
        INSERT INTO sources (run_id, url, canonical_url, domain, source_type, title,
                             accessed_at, content_hash)
        VALUES ($1, 'https://e.test/a', 'https://e.test/a', 'e.test', 'web', 'T', now(), 'h1')
        RETURNING id
        """,
        run_id,
    )
    document_id = await raw.fetchval(
        """
        INSERT INTO documents (source_id, normalized_content, content_hash)
        VALUES ($1, 'body text', 'd1') RETURNING id
        """,
        source_id,
    )
    claim_id = await raw.fetchval(
        """
        INSERT INTO claims (run_id, text, claim_type, normalized_key, status, first_seen_at)
        VALUES ($1, 'c', 'qualitative', 'k', 'candidate', now()) RETURNING id
        """,
        run_id,
    )

    with pytest.raises(asyncpg.CheckViolationError):
        await raw.execute(
            """
            INSERT INTO evidence (claim_id, document_id, source_id, span_text, span_start,
                                  span_end, stance, extractor_agent, extractor_model)
            VALUES ($1, $2, $3, 'q', 100, 10, 'supports', 'a', 'm')
            """,
            claim_id,
            document_id,
            source_id,
        )


async def test_a_run_has_at_most_one_report(raw: asyncpg.Connection):
    """Two reports for one run would be two answers with no way to say which
    is current."""
    user_id = await seed_user(raw)
    run_id = await seed_run(raw, user_id)
    insert = """
        INSERT INTO reports (run_id, title, status, model, generated_at)
        VALUES ($1, 'r', 'draft', 'm', now())
    """
    await raw.execute(insert, run_id)

    with pytest.raises(asyncpg.UniqueViolationError):
        await raw.execute(insert, run_id)


# --- referential behaviour ------------------------------------------------


async def test_deleting_a_run_removes_everything_it_produced(raw: asyncpg.Connection):
    """A user deleting research must not leave orphaned sources behind."""
    user_id = await seed_user(raw)
    run_id = await seed_run(raw, user_id)
    await raw.execute(
        """
        INSERT INTO sources (run_id, url, canonical_url, domain, source_type, title,
                             accessed_at, content_hash)
        VALUES ($1, 'https://e.test/a', 'https://e.test/a', 'e.test', 'web', 'T', now(), 'h1')
        """,
        run_id,
    )

    await raw.execute("DELETE FROM research_runs WHERE id = $1", run_id)

    assert await raw.fetchval("SELECT count(*) FROM sources WHERE run_id = $1", run_id) == 0


async def test_a_cited_claim_cannot_be_deleted(raw: asyncpg.Connection):
    """ON DELETE RESTRICT: deleting a claim out from under a citation would
    leave a report making a statement nothing supports."""
    user_id = await seed_user(raw)
    run_id = await seed_run(raw, user_id)
    source_id = await raw.fetchval(
        """
        INSERT INTO sources (run_id, url, canonical_url, domain, source_type, title,
                             accessed_at, content_hash)
        VALUES ($1, 'https://e.test/a', 'https://e.test/a', 'e.test', 'web', 'T', now(), 'h1')
        RETURNING id
        """,
        run_id,
    )
    claim_id = await raw.fetchval(
        """
        INSERT INTO claims (run_id, text, claim_type, normalized_key, status, first_seen_at)
        VALUES ($1, 'c', 'qualitative', 'k', 'verified', now()) RETURNING id
        """,
        run_id,
    )
    report_id = await raw.fetchval(
        """
        INSERT INTO reports (run_id, title, status, model, generated_at)
        VALUES ($1, 'r', 'validated', 'm', now()) RETURNING id
        """,
        run_id,
    )
    section_id = await raw.fetchval(
        """
        INSERT INTO report_sections (report_id, kind, heading, ordinal, content_md)
        VALUES ($1, 'key_findings', 'Key Findings', 1, 'text [1]') RETURNING id
        """,
        report_id,
    )
    await raw.execute(
        """
        INSERT INTO citations (report_section_id, claim_id, source_id, ordinal, quote)
        VALUES ($1, $2, $3, 1, 'quote')
        """,
        section_id,
        claim_id,
        source_id,
    )

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await raw.execute("DELETE FROM claims WHERE id = $1", claim_id)


# --- generated columns and search -----------------------------------------


async def test_the_chunk_search_vector_is_generated_by_the_database(
    raw: asyncpg.Connection,
):
    """Generated, so it can never drift out of sync with the text it indexes."""
    user_id = await seed_user(raw)
    run_id = await seed_run(raw, user_id)
    source_id = await raw.fetchval(
        """
        INSERT INTO sources (run_id, url, canonical_url, domain, source_type, title,
                             accessed_at, content_hash)
        VALUES ($1, 'https://e.test/a', 'https://e.test/a', 'e.test', 'web', 'T', now(), 'h2')
        RETURNING id
        """,
        run_id,
    )
    document_id = await raw.fetchval(
        """
        INSERT INTO documents (source_id, normalized_content, content_hash)
        VALUES ($1, 'x', 'd2') RETURNING id
        """,
        source_id,
    )
    await raw.execute(
        "INSERT INTO document_chunks (document_id, chunk_index, content) VALUES ($1, 0, $2)",
        document_id,
        "Paged attention reduces key-value cache fragmentation.",
    )

    matched = await raw.fetchval(
        "SELECT count(*) FROM document_chunks WHERE tsv @@ to_tsquery('english', 'fragmentation')"
    )
    assert matched == 1

    # And it tracks an update to the content it is derived from.
    await raw.execute("UPDATE document_chunks SET content = 'speculative decoding'")
    still_matched = await raw.fetchval(
        "SELECT count(*) FROM document_chunks WHERE tsv @@ to_tsquery('english', 'fragmentation')"
    )
    assert still_matched == 0


async def test_timestamps_are_timezone_aware(raw: asyncpg.Connection):
    """A naive timestamp is a bug waiting for a deployment in another zone."""
    user_id = await seed_user(raw)
    created_at = await raw.fetchval("SELECT created_at FROM users WHERE id = $1", user_id)

    assert isinstance(created_at, dt.datetime)
    assert created_at.tzinfo is not None


async def test_primary_keys_are_generated_by_the_database(raw: asyncpg.Connection):
    """UUIDs from gen_random_uuid(): a client cannot choose or collide with one."""
    first = await seed_user(raw, "one@example.com")
    second = await seed_user(raw, "two@example.com")

    assert isinstance(first, uuid.UUID)
    assert first != second
    assert first.version == 4


async def test_a_uuid_read_through_the_orm_is_a_plain_uuid(database, raw: asyncpg.Connection):
    """Not asyncpg's subclass of it.

    ``asyncpg.pgproto.pgproto.UUID`` passes every isinstance check and every
    Pydantic field, so it travels from a row into the research graph's state
    unnoticed - and then LangGraph's checkpoint serializer, whose allowlist is
    derived from the state's *declared* types, refuses to reconstruct it. The
    value comes back as nothing and a resumed run fails a long way from the
    cause. Found by restarting a worker (Phase 13); fixed in ``app.db.base``.
    """
    user_id = await seed_user(raw, "uuid-shape@example.com")

    async with database.session() as session:
        row = await session.get(UserRow, user_id)
        assert row is not None
        stored = row.id

    assert stored == user_id
    assert type(stored) is uuid.UUID, f"the ORM returned {type(stored).__module__}"


# --- pgvector -------------------------------------------------------------


async def test_the_embedding_column_matches_the_declared_width(
    raw: asyncpg.Connection, postgres: ProvisionedDatabase
):
    if not postgres.has_pgvector:
        pytest.skip(
            "pgvector is not installed on this server. The core schema migration "
            "stands alone; 0002_pgvector_embeddings adds this column."
        )

    column_type = await raw.fetchval(
        """
        SELECT udt_name FROM information_schema.columns
        WHERE table_name = 'document_chunks' AND column_name = 'embedding'
        """
    )
    assert column_type == "vector"

    index = await raw.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_document_chunks_embedding_hnsw'"
    )
    assert index is not None
    assert "hnsw" in index.lower()
    assert "vector_cosine_ops" in index.lower()

    width = await raw.fetchval(
        """
        SELECT format_type(atttypid, atttypmod) FROM pg_attribute
        WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'
        """
    )
    # Against the constant, not against a literal. This is the check that was
    # missing when 0002 sized the column at 1536 for a model the registry never
    # declared: nothing compared the schema's width with the width the code
    # believed, so no embedding could be stored at all and the first symptom was
    # a pgvector error on the first write, one phase later.
    #
    # Written this way it also tracks a deliberate change: move
    # EMBEDDING_DIMENSIONS without shipping the migration beside it and this
    # fails, naming both numbers.
    assert width == f"vector({EMBEDDING_DIMENSIONS})", (
        f"the column is {width} but app.db.models.source.EMBEDDING_DIMENSIONS is "
        f"{EMBEDDING_DIMENSIONS}. Changing the embedding model family needs a "
        "resize migration (see migrations/embedding_width.py) and a re-embed."
    )


def test_a_resize_drops_the_index_before_it_changes_the_type():
    """The order is the whole content of a resize, and three of the four ways to
    get it wrong are silent.

    The index goes first because it is built over vectors of the width that is
    about to stop existing. The rest are the quiet ones: clearing the old
    vectors *after* the ALTER means asking pgvector to cast vectors of a width
    it does not accept; not clearing them at all leaves rows in the index that
    no query can meaningfully match, because the distance between vectors from
    two models is a number rather than an answer; and not rebuilding the index
    leaves every search correct and sequential.
    """
    from migrations.embedding_width import INDEX_NAME, statements

    sql = statements(1536)

    assert len(sql) == 4
    drop, clear, alter, create = sql

    assert drop.startswith("DROP INDEX") and INDEX_NAME in drop
    assert clear.startswith("UPDATE document_chunks") and "embedding = NULL" in clear
    # embedding_model too: it is the "is this chunk embedded" marker, and a
    # cleared vector with a model still recorded is a chunk nothing re-embeds.
    assert "embedding_model = NULL" in clear
    assert "ALTER COLUMN embedding TYPE vector(1536)" in alter
    assert create.startswith("CREATE INDEX") and "hnsw" in create
    assert "vector_cosine_ops" in create


@pytest.mark.parametrize("width", [0, -1])
def test_a_resize_to_a_nonsense_width_is_refused(width: int):
    from migrations.embedding_width import statements

    with pytest.raises(ValueError, match="must be positive"):
        statements(width)


async def test_a_resize_really_changes_the_column(
    raw: asyncpg.Connection, postgres: ProvisionedDatabase
):
    """And then the statements are run, because an order that reads correctly
    and does not execute is still a broken migration."""
    if not postgres.has_pgvector:
        pytest.skip("pgvector is not installed on this server; CI runs this.")

    from migrations.embedding_width import statements

    async def width() -> str:
        return await raw.fetchval(
            """
            SELECT format_type(atttypid, atttypmod) FROM pg_attribute
            WHERE attrelid = 'document_chunks'::regclass AND attname = 'embedding'
            """
        )

    assert await width() == f"vector({EMBEDDING_DIMENSIONS})"

    for statement in statements(1536):
        await raw.execute(statement)
    assert await width() == "vector(1536)"

    index = await raw.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_document_chunks_embedding_hnsw'"
    )
    assert index is not None, "the resize left the column unindexed"

    # Back, so this test does not decide the width for whatever runs next.
    for statement in statements(EMBEDDING_DIMENSIONS):
        await raw.execute(statement)
    assert await width() == f"vector({EMBEDDING_DIMENSIONS})"


def test_the_declared_embedding_model_matches_the_index_width():
    """The third side of the triangle, and the one that needs no database.

    Three things have to agree: the width the schema declares, the width the
    chosen embedding model emits, and the width the column actually has. The
    test above compares the first with the third; `ChunkEmbedder` compares the
    first with the second at startup. This one makes that a build failure rather
    than a failure on the first ingestion of a deployment nobody has run yet.
    """
    from app.models.registry import load_registry
    from app.models.routing import ModelRouter

    registry = load_registry(None)
    spec = ModelRouter(registry).embedding_model()

    assert spec.embedding_dimensions == EMBEDDING_DIMENSIONS, (
        f"the registry's embedding model {spec.key} emits "
        f"{spec.embedding_dimensions} dimensions and the index stores "
        f"{EMBEDDING_DIMENSIONS}. Ingestion would refuse to start."
    )


# --- ingestion (Phase 7) --------------------------------------------------

_INSERT_UPLOAD = (
    "INSERT INTO uploads "
    "(user_id, filename, format, mime_type, size_bytes, content_hash, storage_key) "
    "VALUES ($1, 'f', $2, 'application/pdf', 10, 'same-hash', 'k')"
)


async def seed_source(raw: asyncpg.Connection, run_id: uuid.UUID) -> uuid.UUID:
    return await raw.fetchval(
        """
        INSERT INTO sources
            (run_id, url, canonical_url, domain, source_type, title, accessed_at, content_hash)
        VALUES ($1, 'https://example.com', 'https://example.com', 'example.com', 'web', 'T',
                now(), 'h')
        RETURNING id
        """,
        run_id,
    )


async def test_a_document_is_unique_per_source_not_per_database(raw: asyncpg.Connection):
    """Phase 3 made content_hash unique across every user's runs; the same PDF
    could then exist once in the whole system."""
    user = await seed_user(raw)
    first = await seed_source(raw, await seed_run(raw, user))
    second = await seed_source(raw, await seed_run(raw, user))
    insert = (
        "INSERT INTO documents (source_id, normalized_content, content_hash) "
        "VALUES ($1, 'text', 'same-hash')"
    )
    await raw.execute(insert, first)
    await raw.execute(insert, second)
    with pytest.raises(asyncpg.UniqueViolationError):
        await raw.execute(insert, first)


async def test_an_upload_is_unique_per_user_not_globally(raw: asyncpg.Connection):
    alice = await seed_user(raw, "alice@example.com")
    bob = await seed_user(raw, "bob@example.com")
    await raw.execute(_INSERT_UPLOAD, alice, "pdf")
    await raw.execute(_INSERT_UPLOAD, bob, "pdf")
    with pytest.raises(asyncpg.UniqueViolationError):
        await raw.execute(_INSERT_UPLOAD, alice, "pdf")


async def test_an_upload_format_outside_the_vocabulary_is_refused(raw: asyncpg.Connection):
    with pytest.raises(asyncpg.CheckViolationError):
        await raw.execute(_INSERT_UPLOAD, await seed_user(raw), "docx")
