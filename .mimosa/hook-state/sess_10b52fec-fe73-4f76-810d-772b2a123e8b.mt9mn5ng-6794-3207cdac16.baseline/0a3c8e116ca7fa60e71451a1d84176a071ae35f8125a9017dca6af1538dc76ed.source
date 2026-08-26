import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from celery import shared_task
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.analytics import AnalyticsEvent
from app.models.content import Post
from app.models.social_account import SocialAccount


@asynccontextmanager
async def _worker_db():
    engine = create_async_engine(get_settings().DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@shared_task
def sync_all_analytics() -> None:
    asyncio.run(_sync_all_analytics_async())


async def _sync_all_analytics_async() -> None:
    async with _worker_db() as db:
        result = await db.execute(
            select(SocialAccount).where(SocialAccount.status == "active")
        )
        accounts = result.scalars().all()

        for account in accounts:
            try:
                await _sync_account_analytics(account, db)
            except Exception as e:
                print(f"Failed to sync analytics for account {account.id}: {e}")

        await _sync_post_analytics(db)


async def _sync_account_analytics(account: SocialAccount, db: AsyncSession) -> None:
    platform = account.platform.lower()
    if platform == "twitter":
        await _sync_twitter_analytics(account, db)
    elif platform == "linkedin":
        await _sync_linkedin_analytics(account, db)
    elif platform == "instagram":
        await _sync_instagram_analytics(account, db)
    elif platform == "facebook":
        await _sync_facebook_analytics(account, db)


async def _sync_twitter_analytics(account: SocialAccount, db: AsyncSession) -> None:
    pass


async def _sync_linkedin_analytics(account: SocialAccount, db: AsyncSession) -> None:
    pass


async def _sync_instagram_analytics(account: SocialAccount, db: AsyncSession) -> None:
    pass


async def _sync_facebook_analytics(account: SocialAccount, db: AsyncSession) -> None:
    pass


async def _sync_post_analytics(db: AsyncSession) -> None:
    since = datetime.now(UTC) - timedelta(days=30)
    result = await db.execute(
        select(Post).where(
            and_(
                Post.status == "published",
                Post.published_at >= since,
            )
        )
    )
    posts = result.scalars().all()

    for post in posts:
        await db.execute(
            select(
                func.count().filter(AnalyticsEvent.event_type == "impression").label("impressions"),
                func.count().filter(AnalyticsEvent.event_type == "like").label("likes"),
                func.count().filter(AnalyticsEvent.event_type == "comment").label("comments"),
                func.count().filter(AnalyticsEvent.event_type == "share").label("shares"),
                func.count().filter(AnalyticsEvent.event_type == "click").label("clicks"),
            ).where(AnalyticsEvent.post_id == post.id)
        )


@shared_task
def generate_analytics_report(team_id: str, start_date: str, end_date: str) -> None:
    pass
