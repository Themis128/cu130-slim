import os
from pathlib import Path

import pytest

# Only load .env file if not in CI environment
is_ci = os.environ.get("CI") == "true"
if not is_ci:
    # Find the nearest .env walking up — covers both the container layout
    # (/app/tests/… → /app/.env) and host runs (…/backend/tests/… → repo .env).
    env_path = next(
        (p / ".env" for p in Path(__file__).resolve().parents if (p / ".env").exists()),
        None,
    )
    if env_path and env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    # Real env vars take precedence over .env so callers can
                    # override (e.g. point DATABASE_URL at a *_test database).
                    os.environ.setdefault(key, value.strip('"'))

# Override UPLOAD_DIR for tests to a temporary directory
os.environ["UPLOAD_DIR"] = "/tmp/uploads"

# Ensure the uploads directory exists
os.makedirs(os.environ["UPLOAD_DIR"], exist_ok=True)

# Only override database URL for local development (not CI).
# Default to the dedicated ``social_automation_test`` database — never the live
# ``social_automation`` DB, which the ``engine`` fixture would drop.
if not is_ci and "DATABASE_URL" not in os.environ:
    # The social-postgres container is mapped to port 5433 on the host
    os.environ["DATABASE_URL"] = (
        f"postgresql+asyncpg://{os.environ.get('SOCIAL_POSTGRES_USER', 'social_user')}:"
        f"{os.environ.get('SOCIAL_POSTGRES_PASSWORD', 'postgres')}@localhost:5433/social_automation_test"
    )

# Only override redis URL for local development (not CI)
if not is_ci and "REDIS_URL" not in os.environ:
    # Override the redis URL to use the host port for the redis container
    # The redis container is mapped to port 6379 on the host
    os.environ["REDIS_URL"] = "redis://:redis_password@localhost:6379/0"

# Now import the app and other modules
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402


def pytest_collection_modifyitems(items):
    """Mark every test under tests/integration/ so Backend CI can exclude them."""
    for item in items:
        item.add_marker(pytest.mark.integration)


@pytest_asyncio.fixture(scope="function")
async def engine():
    """Create a fresh-schema database engine for each test.

    SAFETY: this fixture runs ``drop_all``/``create_all`` — it must never touch
    a production database. It refuses to run unless the target DB name ends in
    ``_test`` (or ``ALLOW_DESTRUCTIVE_TEST_DB=1`` is set explicitly).
    """
    database_url = os.environ.get("DATABASE_URL", "")
    db_name = database_url.rsplit("/", 1)[-1].split("?")[0]
    if not db_name.endswith("_test") and os.environ.get("ALLOW_DESTRUCTIVE_TEST_DB") != "1":
        pytest.skip(
            f"Refusing drop_all on non-test database '{db_name}' — set DATABASE_URL "
            "to a *_test database (or ALLOW_DESTRUCTIVE_TEST_DB=1 to override)"
        )
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
