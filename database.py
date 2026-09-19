from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

# ---------------------------------------------------------------------------
# Async Engine Setup
# ---------------------------------------------------------------------------
# `create_async_engine` opens a non-blocking connection pool speaking the
# asyncpg wire protocol (DATABASE_URL must start with postgresql+asyncpg://).
#
# - pool_pre_ping=True: issues a lightweight "is this connection alive?"
#   check before handing it out. Cloud Postgres (RDS/Cloud SQL/Supabase)
#   often drops idle connections at the load-balancer level; without this,
#   the first query after an idle period raises a confusing driver error.
# - pool_size / max_overflow: tuned conservatively for a single API
#   instance. Bump these (and consider PgBouncer in front of Postgres) as
#   you scale to multiple app replicas, since each replica gets its own pool.
# - echo=False: keep SQL logging off in prod; flip to True locally to debug.
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# `async_sessionmaker` is the SQLAlchemy 2.0 factory for AsyncSession objects.
#
# - expire_on_commit=False: by default, SQLAlchemy expires all ORM object
#   attributes after commit() so the next access re-fetches from the DB.
#   In a request/response cycle we usually commit, THEN serialize the object
#   into a Pydantic response model — with expiry on, that serialization
#   would trigger a lazy-load outside the session's async context and
#   raise a MissingGreenlet error. Turning it off avoids that footgun.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Shared declarative base class for all ORM models in the app."""

    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields one AsyncSession per request and always
    closes it afterward. On an unhandled exception mid-request we roll back
    explicitly so a half-finished transaction never lingers on a pooled
    connection (which would silently corrupt the next request to reuse it).
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
