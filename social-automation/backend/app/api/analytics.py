import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import get_current_user
from app.db.session import get_db
from app.models.analytics import AnalyticsEvent
from app.models.content import Post, PostStatus, PostTarget
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User

router = APIRouter()


class OverviewMetrics(BaseModel):
    total_posts: int
    published_posts: int
    scheduled_posts: int
    draft_posts: int
    failed_posts: int
    connected_accounts: int
    total_followers: int
    total_engagement: int


class PostMetrics(BaseModel):
    post_id: uuid.UUID
    platform: str
    impressions: int
    clicks: int
    likes: int
    comments: int
    shares: int
    engagement_rate: float


class AccountMetrics(BaseModel):
    account_id: uuid.UUID
    platform: str
    username: str
    followers: int
    posts_count: int
    total_impressions: int
    total_engagement: int
    avg_engagement_rate: float


@router.get("/overview", response_model=OverviewMetrics)
async def get_overview(
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    since = datetime.now(UTC) - timedelta(days=days)

    # Post counts
    post_counts = await db.execute(
        select(Post.status, func.count(Post.id))
        .where(Post.team_id == team.id, Post.created_at >= since)
        .group_by(Post.status)
    )
    counts = {status: count for status, count in post_counts.all()}

    # Connected accounts
    accounts_result = await db.execute(
        select(SocialAccount).where(SocialAccount.team_id == team.id, SocialAccount.status == "active")
    )
    accounts = accounts_result.scalars().all()

    # Total engagement from analytics
    engagement_result = await db.execute(
        select(
            func.count(AnalyticsEvent.id).filter(AnalyticsEvent.event_type.in_(["like", "comment", "share", "click"]))
        )
        .where(AnalyticsEvent.team_id == team.id, AnalyticsEvent.occurred_at >= since)
    )
    total_engagement = engagement_result.scalar() or 0

    # TODO: Get actual follower counts from platform APIs
    total_followers = 0

    return OverviewMetrics(
        total_posts=sum(counts.values()),
        published_posts=counts.get(PostStatus.PUBLISHED, 0),
        scheduled_posts=counts.get(PostStatus.SCHEDULED, 0),
        draft_posts=counts.get(PostStatus.DRAFT, 0),
        failed_posts=counts.get(PostStatus.FAILED, 0),
        connected_accounts=len(accounts),
        total_followers=total_followers,
        total_engagement=total_engagement,
    )


@router.get("/posts/{post_id}/metrics", response_model=list[PostMetrics])
async def get_post_metrics(
    post_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Post)
        .join(Team)
        .join(TeamMember)
        .where(Post.id == post_id, TeamMember.user_id == current_user.id)
        .options(selectinload(Post.targets).selectinload(PostTarget.social_account))
    )
    post = result.scalar_one_or_none()
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    metrics = []
    for target in post.targets:
        # Get analytics events for this post + account
        events = await db.execute(
            select(AnalyticsEvent.event_type, func.count(AnalyticsEvent.id))
            .where(
                AnalyticsEvent.post_id == post_id,
                AnalyticsEvent.social_account_id == target.social_account_id,
            )
            .group_by(AnalyticsEvent.event_type)
        )
        event_counts = {event_type: count for event_type, count in events.all()}

        impressions = event_counts.get("impression", 0)
        likes = event_counts.get("like", 0)
        comments = event_counts.get("comment", 0)
        shares = event_counts.get("share", 0)
        clicks = event_counts.get("click", 0)
        engagement = likes + comments + shares + clicks

        metrics.append(PostMetrics(
            post_id=post_id,
            platform=target.social_account.platform,
            impressions=impressions,
            clicks=clicks,
            likes=likes,
            comments=comments,
            shares=shares,
            engagement_rate=engagement / impressions if impressions > 0 else 0.0,
        ))

    return metrics


@router.get("/accounts/{account_id}/metrics", response_model=AccountMetrics)
async def get_account_metrics(
    account_id: uuid.UUID,
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SocialAccount)
        .join(Team)
        .join(TeamMember)
        .where(SocialAccount.id == account_id, TeamMember.user_id == current_user.id)
    )
    account = result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    since = datetime.now(UTC) - timedelta(days=days)

    # Posts count
    posts_count = await db.execute(
        select(func.count(PostTarget.id))
        .where(PostTarget.social_account_id == account_id, PostTarget.status == "published")
    )
    posts_count = posts_count.scalar() or 0

    # Engagement
    events = await db.execute(
        select(AnalyticsEvent.event_type, func.count(AnalyticsEvent.id))
        .where(
            AnalyticsEvent.social_account_id == account_id,
            AnalyticsEvent.occurred_at >= since,
        )
        .group_by(AnalyticsEvent.event_type)
    )
    event_counts = {event_type: count for event_type, count in events.all()}

    impressions = event_counts.get("impression", 0)
    engagement = sum(event_counts.get(e, 0) for e in ["like", "comment", "share", "click"])

    return AccountMetrics(
        account_id=account_id,
        platform=account.platform,
        username=account.username or "",
        followers=0,  # TODO: Fetch from platform API
        posts_count=posts_count,
        total_impressions=impressions,
        total_engagement=engagement,
        avg_engagement_rate=engagement / impressions if impressions > 0 else 0.0,
    )


class TopPost(BaseModel):
    post_id: uuid.UUID
    content_text: str | None
    platform: str
    impressions: int
    engagement: int
    engagement_rate: float
    published_at: datetime | None


@router.get("/top-posts", response_model=list[TopPost])
async def get_top_posts(
    limit: int = Query(10, ge=1, le=50),
    platform: str | None = None,
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    since = datetime.now(UTC) - timedelta(days=days)

    query = (
        select(Post)
        .where(Post.team_id == team.id, Post.status == PostStatus.PUBLISHED, Post.created_at >= since)
        .options(selectinload(Post.targets).selectinload(PostTarget.social_account))
    )
    posts_result = await db.execute(query)
    posts = posts_result.scalars().all()

    top: list[TopPost] = []
    for post in posts:
        for target in post.targets:
            if platform and target.social_account.platform != platform:
                continue
            events = await db.execute(
                select(AnalyticsEvent.event_type, func.count(AnalyticsEvent.id))
                .where(
                    AnalyticsEvent.post_id == post.id,
                    AnalyticsEvent.social_account_id == target.social_account_id,
                )
                .group_by(AnalyticsEvent.event_type)
            )
            event_counts = {et: c for et, c in events.all()}
            impressions = event_counts.get("impression", 0)
            engagement = sum(event_counts.get(e, 0) for e in ["like", "comment", "share", "click"])
            top.append(TopPost(
                post_id=post.id,
                content_text=post.content_text,
                platform=target.social_account.platform,
                impressions=impressions,
                engagement=engagement,
                engagement_rate=engagement / impressions if impressions > 0 else 0.0,
                published_at=target.published_at,
            ))

    top.sort(key=lambda p: p.engagement, reverse=True)
    return top[:limit]


@router.get("/reports/export")
async def export_report(
    format: str = Query("csv", pattern="^(csv|json)$"),
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == current_user.id)
    )
    team = result.scalars().first()
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    since = datetime.now(UTC) - timedelta(days=days)

    # Export post analytics
    posts = await db.execute(
        select(Post)
        .where(Post.team_id == team.id, Post.created_at >= since)
        .options(selectinload(Post.targets).selectinload(PostTarget.social_account))
    )
    posts = posts.scalars().all()

    rows = []
    for post in posts:
        for target in post.targets:
            events = await db.execute(
                select(AnalyticsEvent.event_type, func.count(AnalyticsEvent.id))
                .where(
                    AnalyticsEvent.post_id == post.id,
                    AnalyticsEvent.social_account_id == target.social_account_id,
                )
                .group_by(AnalyticsEvent.event_type)
            )
            event_counts = {event_type: count for event_type, count in events.all()}

            impressions = event_counts.get("impression", 0)
            likes = event_counts.get("like", 0)
            comments = event_counts.get("comment", 0)
            shares = event_counts.get("share", 0)
            clicks = event_counts.get("click", 0)

            rows.append({
                "post_id": str(post.id),
                "platform": target.social_account.platform,
                "status": post.status.value,
                "scheduled_at": post.scheduled_at.isoformat() if post.scheduled_at else None,
                "published_at": target.published_at.isoformat() if target.published_at else None,
                "impressions": impressions,
                "likes": likes,
                "comments": comments,
                "shares": shares,
                "clicks": clicks,
                "engagement_rate": (likes + comments + shares + clicks) / impressions if impressions > 0 else 0,
            })

    if format == "csv":
        import csv
        import io
        output = io.StringIO()
        if rows:
            writer = csv.DictWriter(output, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        return {"content": output.getvalue(), "filename": f"analytics_{datetime.now().strftime('%Y%m%d')}.csv", "media_type": "text/csv"}

    return {"content": rows, "filename": f"analytics_{datetime.now().strftime('%Y%m%d')}.json", "media_type": "application/json"}


