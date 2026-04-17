"""Shared fixtures.

Strategy:
- One Postgres testcontainer per session.
- Run Alembic migrations once against that container.
- Each test runs inside an outer transaction with a SAVEPOINT, then rolls back at teardown.
  This keeps tests isolated without truncating tables between every run.
- The FastAPI app's DB dependency is overridden to use the test session.
"""
from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.postgres import PostgresContainer

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _generate_rsa_keypair_b64() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return (
        base64.b64encode(private_pem).decode("ascii"),
        base64.b64encode(public_pem).decode("ascii"),
    )


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    container = PostgresContainer("postgres:16-alpine", driver="asyncpg")
    container.start()
    try:
        yield container
    finally:
        container.stop()


@pytest.fixture(scope="session", autouse=True)
def configure_settings(postgres_container: PostgresContainer) -> Iterator[None]:
    """Set env vars BEFORE app.core.config.get_settings is first called."""
    private_b64, public_b64 = _generate_rsa_keypair_b64()
    db_url = postgres_container.get_connection_url()  # postgresql+asyncpg://...

    os.environ["APP_ENV"] = "test"
    os.environ["APP_DEBUG"] = "true"
    os.environ["DATABASE_URL"] = db_url
    os.environ["JWT_PRIVATE_KEY_PEM_BASE64"] = private_b64
    os.environ["JWT_PUBLIC_KEY_PEM_BASE64"] = public_b64
    os.environ["JWT_ALGORITHM"] = "RS256"
    os.environ["JWT_ACCESS_TTL_MINUTES"] = "15"
    os.environ["JWT_REFRESH_TTL_DAYS"] = "7"
    os.environ["JWT_ISSUER"] = "turnero-test"
    os.environ["CORS_ORIGINS"] = "http://localhost:5173"

    # Reset cached settings/engine in case they were imported earlier.
    from app.core.config import get_settings
    from app.db.session import reset_engine

    get_settings.cache_clear()
    reset_engine()
    yield


@pytest_asyncio.fixture(scope="session")
async def migrated_engine(configure_settings: None) -> AsyncIterator[AsyncEngine]:
    """Create the schema by running Alembic migrations once per session."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    # Run upgrade head in a thread because Alembic uses asyncio.run() internally,
    # and we may already be inside a loop here.
    await asyncio.to_thread(command.upgrade, cfg, "head")

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url, future=True)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(migrated_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Per-test transactional session.

    Uses the SQLAlchemy "join into external transaction" pattern:
    - Open a connection, begin an outer transaction.
    - Bind an AsyncSession to that connection with
      `join_transaction_mode="create_savepoint"` so any `session.commit()`
      inside service code releases a SAVEPOINT instead of committing for real.
    - At teardown, rollback the outer transaction → all writes are wiped.
    """
    async with migrated_engine.connect() as connection:
        outer = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if outer.is_active:
                await outer.rollback()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    from app.db.session import get_db
    from app.main import create_app

    app = create_app()

    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
    app.dependency_overrides.clear()
