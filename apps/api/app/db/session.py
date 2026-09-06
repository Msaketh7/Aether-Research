"""Database engine and session management.

Phase 2 establishes the connection machinery and the health probe; the schema
and repositories arrive in Phase 3 (ADR 0004).

The engine is created lazily so the API can start, serve ``/health`` and report
an honest ``/ready`` while Postgres is down, rather than crash-looping at
import time - which is what a container orchestrator needs in order to
distinguish "starting" from "broken".
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import Settings
from app.core.errors import DependencyUnavailable
from app.core.logging import get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    """Declarative base for every ORM model (populated in Phase 3)."""


class Database:
    """Owns the engine and session factory for one process."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self._engine = create_async_engine(
                self._settings.database_url,
                # Bounded pool: an unbounded one turns a slow query into
                # connection exhaustion (TDD 7.3).
                pool_size=self._settings.db_pool_size,
                max_overflow=self._settings.db_max_overflow,
                pool_timeout=self._settings.db_pool_timeout_seconds,
                # Recycle before a proxy or the server drops an idle connection.
                pool_recycle=1800,
                pool_pre_ping=True,
                echo=False,
            )
        return self._engine

    @property
    def sessionmaker(self) -> async_sessionmaker[AsyncSession]:
        if self._sessionmaker is None:
            self._sessionmaker = async_sessionmaker(
                self.engine,
                expire_on_commit=False,
                autoflush=False,
            )
        return self._sessionmaker

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """A transactional session. Commits on success, rolls back on failure."""
        async with self.sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def check(self) -> bool:
        """Readiness probe. Returns False rather than raising, so the caller
        can report a degraded state instead of a 500."""
        try:
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception as exc:
            logger.warning("database health check failed", extra={"error": str(exc)})
            return False
        return True

    async def require(self) -> None:
        if not await self.check():
            raise DependencyUnavailable("The database is not reachable.")

    async def dispose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None
