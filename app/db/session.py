from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=False,
    # SQLite-specific: allow sharing connection across threads for testing
    connect_args={"check_same_thread": False} if "sqlite" in _settings.database_url else {},
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields an async DB session per request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def create_tables() -> None:
    """Create all tables if they don't exist — used by lifespan on first boot."""
    from app.db.models import Base  # noqa: PLC0415

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
