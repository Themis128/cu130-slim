from celery import shared_task
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, func, and_
from datetime import datetime, UTC, timedelta
import httpx

from app.core.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False)
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@shared_task
async def sync_all_analytics():
    """Sync analytics from all connected social platforms"""
    async with async_session() as db:
from app.models.content import Post, PostStatus
from app.models.social_account import SocialAccount
from app.models.analytics import AnalyticsEvent
        
        # Get all active social accounts
        result = await db.execute(
            select(SocialAccount).where(SocialAccount.is_active == True)
        )
        accounts = result.scalars().all()

        for account in accounts:
            try:
                await sync_account_analytics(account, db)
            except Exception as e:
                # Log error but continue with other accounts
                print(f"Failed to sync analytics for account {account.id}: {e}")

        # Also sync post-level analytics
        await sync_post_analytics(db)


async def sync_account_analytics(account, db):
    """Sync analytics for a specific account"""
    platform = account.platform.lower()
    
    if platform == "twitter":
        await sync_twitter_analytics(account, db)
    elif platform == "linkedin":
        await sync_linkedin_analytics(account, db)
    elif platform == "instagram":
        await sync_instagram_analytics(account, db)
    elif platform == "facebook":
        await sync_facebook_analytics(account, db)


async def sync_twitter_analytics(account, db):
    """Sync Twitter analytics"""
    # This would use Twitter API v2 to get tweet metrics
    # For now, placeholder implementation
    pass


async def sync_linkedin_analytics(account, db):
    """Sync LinkedIn analytics"""
    # This would use LinkedIn API to get post metrics
    pass


async def sync_instagram_analytics(account, db):
    """Sync Instagram analytics"""
    # This would use Instagram Graph API
    pass


async def sync_facebook_analytics(account, db):
    """Sync Facebook analytics"""
    # This would use Facebook Graph API
    pass


async def sync_post_analytics(db):
    """Sync analytics for published posts"""
    # Get posts published in last 30 days
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
        # Aggregate analytics events for this post
        events_result = await db.execute(
            select(
                func.count().filter(AnalyticsEvent.event_type == "impression").label("impressions"),
                func.count().filter(AnalyticsEvent.event_type == "like").label("likes"),
                func.count().filter(AnalyticsEvent.event_type == "comment").label("comments"),
                func.count().filter(AnalyticsEvent.event_type == "share").label("shares"),
                func.count().filter(AnalyticsEvent.event_type == "click").label("clicks"),
            ).where(AnalyticsEvent.post_id == post.id)
        )
        # Update post metrics
        # This would update the post with aggregated metrics
        pass


@shared_task
async def generate_analytics_report(team_id: str, start_date: str, end_date: str):
    """Generate analytics report for a team"""
    # This would generate a comprehensive report
    # and potentially save it as a file or send via email
    pass