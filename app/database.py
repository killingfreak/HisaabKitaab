"""
database.py
Async database engine + session management for HisaabKitaab.

We use SQLAlchemy 2.0's async engine backed by `asyncpg`, the fastest
PostgreSQL driver for asyncio. This module is intentionally the single
place that turns validated `Settings` into an actual engine/session —
the rest of the app only ever imports `get_db` (a FastAPI dependency)
or `engine` (for startup/shutdown hooks, migrations, etc).
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sqlalchemy.orm import DeclarativeBase
from app.config import settings
# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
# `pool_pre_ping` guards against stale connections after DB restarts /
# cloud provider failovers (common on managed Postgres like RDS/Cloud SQL).
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.SQL_ECHO,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------
# `expire_on_commit=False` is important for async: it stops SQLAlchemy from
# lazily re-fetching attributes after commit (which would trigger implicit
# I/O outside of an awaited context and raise MissingGreenlet errors).
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields a request-scoped AsyncSession.

    Usage:
        @app.get("/items")
        async def list_items(db: AsyncSession = Depends(get_db)):
            ...

    The session is always closed at the end of the request, and rolled
    back automatically if an unhandled exception propagates out of the
    endpoint (SQLAlchemy's async context manager handles this for us).
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# class Base(DeclarativeBase):
#     """Shared declarative base class for all ORM models in the app."""

#     pass
