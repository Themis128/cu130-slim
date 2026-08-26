import uuid
from collections.abc import AsyncGenerator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.security import hash_password
from app.models.user import User

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    from app.db.base import Base
    settings = get_settings()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Seed admin user if configured
    if settings.SOCIAL_ADMIN_EMAIL and settings.SOCIAL_ADMIN_PASSWORD:
        async with async_session_maker() as session:
            result = await session.execute(
                select(User).where(User.email == settings.SOCIAL_ADMIN_EMAIL)
            )
            existing = result.scalar_one_or_none()
            if not existing:
                admin_user = User(
                    id=uuid.uuid4(),
                    email=settings.SOCIAL_ADMIN_EMAIL,
                    password_hash=hash_password(settings.SOCIAL_ADMIN_PASSWORD),
                    name=settings.SOCIAL_ADMIN_NAME or "Admin User",
                )
                session.add(admin_user)
                await session.commit()
                print(f"Seeded admin user: {settings.SOCIAL_ADMIN_EMAIL}")
