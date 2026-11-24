from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


def make_engine(db_url: str | None = None):
    url = db_url or settings.database_url
    return create_async_engine(url, echo=False, pool_pre_ping=True)


engine = make_engine()

SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


def make_session_factory(engine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncSession:
    async with SessionFactory() as session:
        yield session
