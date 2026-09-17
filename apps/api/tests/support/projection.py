"""Rows a projection needs before it can run: a user, a run, its sources.

Shared by the evidence and report projection suites, which both need a run whose
sources really exist - a citation's foreign keys are ``ON DELETE RESTRICT``, so
there is no way to test either projection against invented ids.

Written as rows rather than through ingestion on purpose: ingestion needs bytes,
a parser and object storage, and all these tests need is a source with a hash, an
excerpt and a publisher. The pipeline that really writes them has its own suite,
including the assertion that it stores the credibility shape used here.
"""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa

from app.core.enums import ResearchMode, RunStatus, SourceType
from app.db.models.research import ResearchRunRow
from app.db.models.source import DocumentRow, SourceRow
from app.db.repositories.user import UserRepository
from app.db.session import Database
from app.sources.credibility import assess

#: Two passages that share no vocabulary, so a test that wants two distinct
#: sources gets them rather than a near-duplicate cluster.
WIRE = (
    "The company said quarterly data center revenue reached thirty five point six "
    "billion dollars, up from twenty two point six billion a year earlier, as "
    "demand for inference accelerators continued to outstrip supply everywhere."
)
OTHER = (
    "A research firm argued in a note that inference pricing will fall next year "
    "as new capacity arrives, and advised clients to delay long term commitments "
    "until the second half of the year."
)

ACCESSED = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)


async def seed_user(database: Database) -> uuid.UUID:
    user_id = uuid.uuid4()
    async with database.session() as session:
        await UserRepository(session).ensure(user_id, f"{user_id}@example.test")
    return user_id


async def seed_run(database: Database, user_id: uuid.UUID) -> uuid.UUID:
    async with database.session() as session:
        run = ResearchRunRow(
            user_id=user_id,
            title="Inference pricing",
            question="What does inference cost?",
            mode=ResearchMode.DEEP.value,
            status=RunStatus.RESEARCHING.value,
        )
        session.add(run)
        await session.flush()
        return run.id


async def seed_source(
    database: Database,
    run_id: uuid.UUID,
    name: str,
    *,
    url: str | None = None,
    digest: str | None = None,
    excerpt: str = WIRE,
    title: str | None = None,
    source_type: SourceType = SourceType.WEB,
    domain: str = "example.test",
    minutes: int = 0,
) -> tuple[uuid.UUID, uuid.UUID]:
    """One source and one document for it. Returns ``(source_id, document_id)``."""
    credibility = assess(source_type, domain)
    async with database.session() as session:
        source = SourceRow(
            run_id=run_id,
            url=url or f"https://{name}.test/story",
            canonical_url=url or f"https://{name}.test/story",
            domain=domain,
            source_type=source_type.value,
            title=title or f"Report from {name}",
            publisher=domain,
            accessed_at=ACCESSED + dt.timedelta(minutes=minutes),
            content_hash=digest or f"hash-{name}",
            credibility_score=credibility.score,
            credibility_metadata=credibility.as_metadata(),
            excerpt=excerpt,
        )
        session.add(source)
        await session.flush()
        document = DocumentRow(
            source_id=source.id,
            normalized_content=excerpt,
            content_hash=digest or f"hash-{name}",
        )
        session.add(document)
        await session.flush()
        return source.id, document.id


async def count(database: Database, table: str, run_id: uuid.UUID | None = None) -> int:
    where = " WHERE run_id = :run_id" if run_id else ""
    async with database.session() as session:
        result = await session.execute(
            sa.text(f"SELECT count(*) FROM {table}{where}"),  # noqa: S608 - fixed table names
            {"run_id": run_id} if run_id else {},
        )
        return int(result.scalar_one())
