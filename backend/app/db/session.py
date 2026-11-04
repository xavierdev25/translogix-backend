from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.shared.config import Settings


def create_engine(settings: Settings, **pool: Any) -> AsyncEngine:
    """``pool``: pool_size, max_overflow, pool_timeout (2.0.0, CAP-01); SQLAlchemy defaults
    otherwise."""
    return create_async_engine(str(settings.database_url), pool_pre_ping=True, **pool)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        yield session
