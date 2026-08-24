import os
from pathlib import Path

# Only load .env file if not in CI environment
is_ci = os.environ.get("CI") == "true"
if not is_ci:
    # Load environment variables from .env file if it exists
    env_path = Path(__file__).parent.parent.parent / '.env'
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key] = value.strip('"')

# Override UPLOAD_DIR for tests to a temporary directory
os.environ["UPLOAD_DIR"] = "/tmp/uploads"

# Ensure the uploads directory exists
os.makedirs(os.environ["UPLOAD_DIR"], exist_ok=True)

# Only override database URL for local development (not CI)
if not is_ci and "DATABASE_URL" not in os.environ:
    # Override the database URL to use the host port for the social-postgres container
    # The social-postgres container is mapped to port 5433 on the host
    os.environ["DATABASE_URL"] = "postgresql+asyncpg://postgres:postgres_password@localhost:5433/social"

# Only override redis URL for local development (not CI)
if not is_ci and "REDIS_URL" not in os.environ:
    # Override the redis URL to use the host port for the redis container
    # The redis container is mapped to port 6379 on the host
    os.environ["REDIS_URL"] = "redis://:redis_password@localhost:6379/0"

# Now import the app and other modules
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app


@pytest_asyncio.fixture(scope="function")
async def engine():
    """Create a new database engine for each test."""
    database_url = os.environ.get("DATABASE_URL")
    print(f"Using DATABASE_URL: {database_url}")
    eng = create_async_engine(database_url, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(scope="function")
async def db(engine):
    """Get a database session for each test."""
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture(scope="function")
async def client(db):
    """Get an HTTP client for each test."""
    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()