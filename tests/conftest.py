"""
Pytest configuration.

The environment is pinned BEFORE any app module is imported so that every test talks to a throwaway
SQLite file and never to the developer's selaras.db, real API keys, or the live Telegram bot.
"""

from __future__ import annotations

import os
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="selaras-test-")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DIR}/test.db"
os.environ["DEMO_MODE"] = "true"
os.environ["PORTAL_PASSWORD"] = ""
os.environ["GEMINI_API_KEY"] = ""
os.environ["NVIDIA_NIM_API_KEY"] = ""
os.environ["TELEGRAM_BOT_TOKEN"] = ""

import pytest  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from app.db.models import Base  # noqa: E402


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
async def _dispose_engine_after_test():
    """aiosqlite connections are bound to the loop that opened them; never reuse them across tests."""
    yield
    from app.db.session import engine

    await engine.dispose()


@pytest.fixture
async def seeded_db():
    """Fresh demo data (companies, officers, cases, risk scores) in the throwaway DB."""
    from app.db.session import create_tables
    from synth.seed_demo import seed

    await create_tables()
    await seed()


@pytest.fixture
async def client(seeded_db):
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t") as c:
        yield c


@pytest.fixture(scope="function")
async def db_session():
    """Provide a clean async DB session backed by an in-memory SQLite DB."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()
