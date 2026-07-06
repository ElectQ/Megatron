from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import event

from ..config import settings
from .logging import get_logger

logger = get_logger(__name__)

engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    # WAL + a busy timeout let the scheduler and web writers share the file
    # without immediate "database is locked" errors.
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def insert_ignore(model, values, index_elements):
    """Build a dialect-appropriate ``INSERT ... ON CONFLICT DO NOTHING``.

    Supports the two configured backends — SQLite (aiosqlite) and PostgreSQL
    (asyncpg) — both of which expose ``on_conflict_do_nothing``. Other dialects
    are rejected explicitly rather than silently emitting a plain INSERT that
    would raise on duplicate keys. ``values`` may be a single mapping or a list
    of mappings for a multi-row insert.
    """
    name = engine.dialect.name
    if name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as _insert
    elif name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as _insert
    else:
        raise NotImplementedError(f"insert_ignore not supported for dialect '{name}'")
    return _insert(model).values(values).on_conflict_do_nothing(index_elements=index_elements)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db() -> None:
    """Create tables on startup (dev simplicity; Alembic later)."""
    from .models import Base  # noqa: F401  triggers registration
    from . import engine_models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("db.init", url=settings.database_url)


async def dispose_db() -> None:
    await engine.dispose()
    logger.info("db.disposed")


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
