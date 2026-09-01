import uuid
from collections.abc import AsyncGenerator

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.security import hash_password, verify_password
from app.models.user import User

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    hide_parameters=True,
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
    from app.models.user import Team, TeamMember, UserRole

    settings = get_settings()
    # Schema is managed exclusively by Alembic (run via compose command before uvicorn).
    # Seed admin user if configured, and always ensure they own a team.
    # Registration creates a team; the env-seeded admin path previously did not,
    # which caused "Failed to connect" when linking social accounts.
    if settings.SOCIAL_ADMIN_EMAIL and settings.SOCIAL_ADMIN_PASSWORD:
        async with async_session_maker() as session:
            result = await session.execute(
                select(User).where(User.email == settings.SOCIAL_ADMIN_EMAIL)
            )
            admin_user = result.scalar_one_or_none()
            if not admin_user:
                try:
                    admin_user = User(
                        id=uuid.uuid4(),
                        email=settings.SOCIAL_ADMIN_EMAIL,
                        password_hash=hash_password(settings.SOCIAL_ADMIN_PASSWORD),
                        name=settings.SOCIAL_ADMIN_NAME or "Admin User",
                        timezone=settings.APP_TIMEZONE,
                    )
                    session.add(admin_user)
                    await session.flush()
                    print(f"Seeded admin user: {settings.SOCIAL_ADMIN_EMAIL}")
                except IntegrityError:
                    await session.rollback()
                    result = await session.execute(
                        select(User).where(User.email == settings.SOCIAL_ADMIN_EMAIL)
                    )
                    admin_user = result.scalar_one_or_none()

            if admin_user is None:
                # Defensive: should not happen, but don't continue if the user still can't be resolved.
                await session.rollback()
                return

            # Keep the env-seeded admin permanently in sync with the current config so
            # these credentials are always accepted: re-hash the password and refresh
            # name/timezone on every startup (e.g. after .env rotation or a DB re-import).
            try:
                password_matches = verify_password(
                    settings.SOCIAL_ADMIN_PASSWORD, admin_user.password_hash
                )
            except Exception:
                password_matches = False
            desired_name = settings.SOCIAL_ADMIN_NAME or "Admin User"
            if (
                not password_matches
                or admin_user.name != desired_name
                or admin_user.timezone != settings.APP_TIMEZONE
            ):
                admin_user.password_hash = hash_password(settings.SOCIAL_ADMIN_PASSWORD)
                admin_user.name = desired_name
                admin_user.timezone = settings.APP_TIMEZONE
                print(f"Synced env admin credentials for {settings.SOCIAL_ADMIN_EMAIL}")

            membership = await session.execute(
                select(TeamMember).where(TeamMember.user_id == admin_user.id)
            )
            if not membership.scalar_one_or_none():
                try:
                    team = Team(
                        name=f"{admin_user.name or admin_user.email}'s Team",
                        owner_id=admin_user.id,
                    )
                    session.add(team)
                    await session.flush()
                    session.add(
                        TeamMember(team_id=team.id, user_id=admin_user.id, role=UserRole.OWNER)
                    )
                    print(f"Seeded default team for admin: {settings.SOCIAL_ADMIN_EMAIL}")
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
            else:
                await session.commit()
