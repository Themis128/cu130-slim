"""Analytics API — metrics from self-hosted Postgres (AnalyticsEvent), no cloud analytics APIs."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import Integer, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.auth import get_current_user
from app.core.config import settings
from app.core.security import decrypt_token
from app.db.session import get_db
from app.models.analytics import AnalyticsEvent, PostAnalyticsSnapshot
from app.models.content import Post, PostStatus, PostTarget
from app.models.social_account import SocialAccount
from app.models.user import Team, TeamMember, User
from app.services.analytics_sync import sync_team_analytics
from app.services.linkedin_api import LinkedInAPIClient
from app.worker.tasks.analytics import sync_team_analytics_task

router = APIRouter()

ENGAGEMENT_TYPES = ("like", "comment", "share", "click")

def _event_count_expr():
    """Prefer meta_data.count (platform sync); else each row counts as 1."""
    return func.coalesce(cast(AnalyticsEvent.meta_data["count"].astext, Integer), 1)




async def _team_for_user(db: AsyncSession, user: User) -> Team | None:
    result = await db.execute(
        select(Team).join(TeamMember).where(TeamMember.user_id == user.id)
    )
    return result.scalars().first()


def _athens_day_expr():
    """Calendar day of an event in APP_TIMEZONE (Europe/Athens)."""
    # timestamptz → local timestamp in Athens → truncate to day
    return func.date_trunc(
        "day",
        func.timezone(settings.APP_TIMEZONE, AnalyticsEvent.occurred_at),
    )


def _engagement_sum(event_counts: dict[str, int]) -> int:
    return sum(event_counts.get(e, 0) for e in ENGAGEMENT_TYPES)


def _engagement_rate(engagement: int, impressions: int) -> float:
    return engagement / impressions if impressions > 0 else 0.0


def _org_urn(account: SocialAccount) -> str:
    """Build the LinkedIn organization URN for a Company Page account."""
    meta = account.meta_data or {}
    if meta.get("author_urn") and str(meta["author_urn"]).startswith("urn:li:organization:"):
        return str(meta["author_urn"])
    return f"urn:li:organization:{account.account_id}"


async def _linkedin_follower_count(account: SocialAccount) -> int:
    """Fetch live follower count for a LinkedIn Company Page account."""
    if account.platform != "linkedin":
        return 0
    try:
        token = decrypt_token(account.access_token_enc)
        client = LinkedInAPIClient(access_token=token)
        return await client.get_follower_count(_org_urn(account))
    except Exception:
        return 0


async def _twitter_follower_count(account: SocialAccount) -> int:
    """Fetch follower count for a Twitter/X account via API v2."""
    if account.platform != "twitter":
        return 0
    try:
        import httpx
        token = decrypt_token(account.access_token_enc)
        headers = {"Authorization": f"Bearer {token}"}
        # Use the account_id (Twitter user ID) to fetch user info
        user_id = account.account_id
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://api.twitter.com/2/users/{user_id}",
                headers=headers,
                params={"user.fields": "public_metrics"},
            )
            if resp.status_code == 200:
                data = (resp.json() or {}).get("data", {})
                return int((data.get("public_metrics") or {}).get("followers_count", 0) or 0)
    except Exception:
        pass
    return 0


async def _facebook_follower_count(account: SocialAccount) -> int:
    """Fetch follower count for a Facebook Page via Graph API."""
    if account.platform != "facebook":
        return 0
    try:
        import httpx
        token = decrypt_token(account.access_token_enc)
        page_id = account.account_id
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://graph.facebook.com/v20.0/{page_id}",
                params={"fields": "followers_count,fan_count", "access_token": token},
            )
            if resp.status_code == 200:
                data = resp.json() or {}
                return int(data.get("followers_count", 0) or data.get("fan_count", 0) or 0)
    except Exception:
        pass
    return 0


async def _instagram_follower_count(account: SocialAccount) -> int:
    """Fetch follower count for an Instagram Business/Creator account."""
    if account.platform != "instagram":
        return 0
    try:
        import httpx
        token = decrypt_token(account.access_token_enc)
        ig_user_id = account.account_id
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://graph.facebook.com/v20.0/{ig_user_id}",
                params={"fields": "followers_count", "access_token": token},
            )
            if resp.status_code == 200:
                data = resp.json() or {}
                return int(data.get("followers_count", 0) or 0)
    except Exception:
        pass
    return 0


async def _threads_follower_count(account: SocialAccount) -> int:
    """Fetch follower count for a Threads profile."""
    if account.platform != "threads":
        return 0
    try:
        import httpx
        token = decrypt_token(account.access_token_enc)
        threads_user_id = account.account_id
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"https://graph.threads.net/v1.0/{threads_user_id}",
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": "followers_count"},
            )
            if resp.status_code == 200:
                data = resp.json() or {}
                return int(data.get("followers_count", 0) or 0)
    except Exception:
        pass
    return 0


async def _tiktok_follower_count(account: SocialAccount) -> int:
    """Fetch follower count for a TikTok account via Display API."""
    if account.platform != "tiktok":
        return 0
    try:
        import httpx
        token = decrypt_token(account.access_token_enc)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://open.tiktokapis.com/v2/user/info/",
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": "follower_count"},
            )
            if resp.status_code == 200:
                data = (resp.json() or {}).get("data", {})
                return int((data.get("user") or {}).get("follower_count", 0) or 0)
    except Exception:
        pass
    return 0


async def _follower_count(account: SocialAccount) -> int:
    """Dispatch to the correct platform follower count function."""
    dispatch = {
        "linkedin": _linkedin_follower_count,
        "twitter": _twitter_follower_count,
        "facebook": _facebook_follower_count,
        "instagram": _instagram_follower_count,
        "threads": _threads_follower_count,
        "tiktok": _tiktok_follower_count,
    }
    fn = dispatch.get(account.platform)
    if fn is None:
        return 0
    return await fn(account)


def _latest_snapshot_ids_subq(team_id, since: datetime | None = None, *, posts_only: bool = False):
    """IDs of the newest snapshot per platform_post_id by captured_at."""
    filters = [PostAnalyticsSnapshot.team_id == team_id]
    if since is not None:
        filters.append(PostAnalyticsSnapshot.captured_at >= since)
    if posts_only:
        # Exclude org-lifetime aggregates from post rankings
        filters.append(PostAnalyticsSnapshot.source != "linkedin_org_lifetime")
        filters.append(PostAnalyticsSnapshot.platform_post_id.isnot(None))
    ranked = (
        select(
            PostAnalyticsSnapshot.id,
            func.row_number()
            .over(
                partition_by=(
                    PostAnalyticsSnapshot.social_account_id,
                    PostAnalyticsSnapshot.platform_post_id,
                ),
                order_by=PostAnalyticsSnapshot.captured_at.desc(),
            )
            .label("rn"),
        )
        .where(*filters)
        .subquery()
    )
    return select(ranked.c.id).where(ranked.c.rn == 1)


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


class TopPost(BaseModel):
    post_id: uuid.UUID
    content_text: str | None
    platform: str
    impressions: int
    engagement: int
    engagement_rate: float
    published_at: datetime | None


class EngagementPoint(BaseModel):
    date: str
    likes: int
    comments: int
    shares: int
    clicks: int
    total: int


class FollowerPoint(BaseModel):
    platform: str
    followers: int
    change: int


class PlatformMetrics(BaseModel):
    platform: str
    posts_count: int
    published_count: int
    scheduled_count: int
    total_engagement: int
    total_impressions: int
    engagement_rate: float


@router.get("/overview", response_model=OverviewMetrics)
async def get_overview(
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _team_for_user(db, current_user)
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    since = datetime.now(UTC) - timedelta(days=days)

    post_counts = await db.execute(
        select(Post.status, func.count(Post.id))
        .where(Post.team_id == team.id, Post.created_at >= since)
        .group_by(Post.status)
    )
    counts = {status: count for status, count in post_counts.all()}

    accounts_count = await db.execute(
        select(func.count(SocialAccount.id)).where(
            SocialAccount.team_id == team.id,
            SocialAccount.status == "active",
        )
    )

    # Fetch live follower counts from LinkedIn for active accounts
    accounts_result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.team_id == team.id,
            SocialAccount.status == "active",
        )
    )
    accounts = accounts_result.scalars().all()
    total_followers = 0
    for account in accounts:
        total_followers += await _follower_count(account)

    # Prefer latest snapshots when present; else event counters (with meta_data.count)
    snap_eng = await db.execute(
        select(func.coalesce(func.sum(PostAnalyticsSnapshot.engagement), 0)).where(
            PostAnalyticsSnapshot.id.in_(
                _latest_snapshot_ids_subq(team.id, since, posts_only=True)
            ),
        )
    )
    total_from_snaps = int(snap_eng.scalar() or 0)
    if total_from_snaps == 0:
        engagement_result = await db.execute(
            select(func.coalesce(func.sum(_event_count_expr()), 0)).where(
                AnalyticsEvent.team_id == team.id,
                AnalyticsEvent.occurred_at >= since,
                AnalyticsEvent.event_type.in_(list(ENGAGEMENT_TYPES)),
            )
        )
        total_engagement = int(engagement_result.scalar() or 0)
    else:
        total_engagement = total_from_snaps

    return OverviewMetrics(
        total_posts=sum(counts.values()),
        published_posts=counts.get(PostStatus.PUBLISHED, 0),
        scheduled_posts=counts.get(PostStatus.SCHEDULED, 0),
        draft_posts=counts.get(PostStatus.DRAFT, 0),
        failed_posts=counts.get(PostStatus.FAILED, 0),
        connected_accounts=accounts_count.scalar() or 0,
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

    events = await db.execute(
        select(
            AnalyticsEvent.social_account_id,
            AnalyticsEvent.event_type,
            func.sum(_event_count_expr()),
        )
        .where(AnalyticsEvent.post_id == post_id)
        .group_by(AnalyticsEvent.social_account_id, AnalyticsEvent.event_type)
    )
    by_account: dict[uuid.UUID, dict[str, int]] = {}
    for account_id, event_type, cnt in events.all():
        by_account.setdefault(account_id, {})[event_type] = cnt

    metrics: list[PostMetrics] = []
    for target in post.targets:
        event_counts = by_account.get(target.social_account_id, {})
        impressions = event_counts.get("impression", 0)
        likes = event_counts.get("like", 0)
        comments = event_counts.get("comment", 0)
        shares = event_counts.get("share", 0)
        clicks = event_counts.get("click", 0)
        engagement = likes + comments + shares + clicks
        metrics.append(
            PostMetrics(
                post_id=post_id,
                platform=target.social_account.platform,
                impressions=impressions,
                clicks=clicks,
                likes=likes,
                comments=comments,
                shares=shares,
                engagement_rate=_engagement_rate(engagement, impressions),
            )
        )
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

    posts_count = (
        await db.execute(
            select(func.count(PostTarget.post_id)).where(
                PostTarget.social_account_id == account_id,
                PostTarget.status == "published",
            )
        )
    ).scalar() or 0

    events = await db.execute(
        select(AnalyticsEvent.event_type, func.sum(_event_count_expr()))
        .where(
            AnalyticsEvent.social_account_id == account_id,
            AnalyticsEvent.occurred_at >= since,
        )
        .group_by(AnalyticsEvent.event_type)
    )
    event_counts = {event_type: int(count or 0) for event_type, count in events.all()}
    impressions = event_counts.get("impression", 0)
    engagement = _engagement_sum(event_counts)

    # Fetch live follower count for this account
    followers = await _follower_count(account)

    return AccountMetrics(
        account_id=account_id,
        platform=account.platform,
        username=account.username or "",
        followers=followers,
        posts_count=posts_count,
        total_impressions=impressions,
        total_engagement=engagement,
        avg_engagement_rate=_engagement_rate(engagement, impressions),
    )


@router.get("/top-posts", response_model=list[TopPost])
async def get_top_posts(
    limit: int = Query(10, ge=1, le=50),
    platform: str | None = None,
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _team_for_user(db, current_user)
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    since = datetime.now(UTC) - timedelta(days=days)

    # Prefer latest platform-synced snapshots when available
    snap_q = (
        select(PostAnalyticsSnapshot, Post.content_text)
        .outerjoin(Post, Post.id == PostAnalyticsSnapshot.post_id)
        .where(
            PostAnalyticsSnapshot.id.in_(
                _latest_snapshot_ids_subq(team.id, since, posts_only=True)
            )
        )
        .order_by(PostAnalyticsSnapshot.engagement.desc())
        .limit(limit)
    )
    if platform:
        snap_q = snap_q.where(PostAnalyticsSnapshot.platform == platform)
    snap_rows = (await db.execute(snap_q)).all()
    if snap_rows:
        return [
            TopPost(
                post_id=snap.post_id or snap.id,
                content_text=content_text
                or ((snap.raw or {}).get("discovery") or {}).get("commentary")
                or snap.platform_post_id,
                platform=snap.platform,
                impressions=snap.impressions,
                engagement=snap.engagement,
                engagement_rate=snap.engagement_rate,
                published_at=None,
            )
            for snap, content_text in snap_rows
            if snap.source != "linkedin_org_lifetime"
        ]

    impressions_col = func.sum(
        case((AnalyticsEvent.event_type == "impression", _event_count_expr()), else_=0)
    ).label("impressions")
    engagement_col = func.sum(
        case(
            (AnalyticsEvent.event_type.in_(list(ENGAGEMENT_TYPES)), _event_count_expr()),
            else_=0,
        )
    ).label("engagement")

    query = (
        select(
            Post.id,
            Post.content_text,
            SocialAccount.platform,
            PostTarget.published_at,
            impressions_col,
            engagement_col,
        )
        .join(PostTarget, PostTarget.post_id == Post.id)
        .join(SocialAccount, SocialAccount.id == PostTarget.social_account_id)
        .outerjoin(
            AnalyticsEvent,
            (AnalyticsEvent.post_id == Post.id)
            & (AnalyticsEvent.social_account_id == PostTarget.social_account_id)
            & (AnalyticsEvent.occurred_at >= since),
        )
        .where(
            Post.team_id == team.id,
            Post.status == PostStatus.PUBLISHED,
            Post.created_at >= since,
        )
        .group_by(Post.id, Post.content_text, SocialAccount.platform, PostTarget.published_at)
        .order_by(engagement_col.desc().nulls_last())
        .limit(limit)
    )
    if platform:
        query = query.where(SocialAccount.platform == platform)

    rows = await db.execute(query)
    top: list[TopPost] = []
    for post_id, content_text, plat, published_at, impressions, engagement in rows.all():
        imp = int(impressions or 0)
        eng = int(engagement or 0)
        top.append(
            TopPost(
                post_id=post_id,
                content_text=content_text,
                platform=plat,
                impressions=imp,
                engagement=eng,
                engagement_rate=_engagement_rate(eng, imp),
                published_at=published_at,
            )
        )
    return top


@router.get("/engagement", response_model=list[EngagementPoint])
async def get_engagement_trends(
    days: int = Query(30, ge=1, le=365),
    platform: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _team_for_user(db, current_user)
    if not team:
        return []

    since = datetime.now(UTC) - timedelta(days=days)
    day_expr = _athens_day_expr()

    query = (
        select(
            day_expr.label("day"),
            AnalyticsEvent.event_type,
            func.coalesce(func.sum(_event_count_expr()), 0).label("cnt"),
        )
        .where(AnalyticsEvent.team_id == team.id, AnalyticsEvent.occurred_at >= since)
        .group_by("day", AnalyticsEvent.event_type)
        .order_by("day")
    )
    if platform:
        query = query.join(
            SocialAccount, SocialAccount.id == AnalyticsEvent.social_account_id
        ).where(SocialAccount.platform == platform)

    rows = await db.execute(query)
    by_day: dict[str, dict[str, int]] = {}
    for day, event_type, cnt in rows.all():
        key = day.strftime("%Y-%m-%d") if hasattr(day, "strftime") else str(day)[:10]
        if key not in by_day:
            by_day[key] = {"likes": 0, "comments": 0, "shares": 0, "clicks": 0}
        mapped = {
            "like": "likes",
            "comment": "comments",
            "share": "shares",
            "click": "clicks",
        }.get(event_type)
        if mapped:
            by_day[key][mapped] = int(cnt or 0)

    return [
        EngagementPoint(
            date=d,
            likes=v["likes"],
            comments=v["comments"],
            shares=v["shares"],
            clicks=v["clicks"],
            total=sum(v.values()),
        )
        for d, v in sorted(by_day.items())
    ]


@router.get("/followers", response_model=list[FollowerPoint])
async def get_follower_counts(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _team_for_user(db, current_user)
    if not team:
        return []

    accounts_result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.team_id == team.id, SocialAccount.status == "active"
        )
    )
    accounts = accounts_result.scalars().all()
    # Fetch live follower counts from all platforms
    result: list[FollowerPoint] = []
    for account in accounts:
        followers = await _follower_count(account)
        result.append(FollowerPoint(platform=account.platform, followers=followers, change=0))
    return result


@router.get("/platforms", response_model=list[PlatformMetrics])
async def get_platform_metrics(
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _team_for_user(db, current_user)
    if not team:
        return []

    since = datetime.now(UTC) - timedelta(days=days)

    accounts_result = await db.execute(
        select(SocialAccount).where(SocialAccount.team_id == team.id)
    )
    accounts = accounts_result.scalars().all()
    platforms_seen = {a.platform for a in accounts} or {
        "linkedin",
        "twitter",
        "instagram",
        "facebook",
        "threads",
        "tiktok",
    }

    # One grouped query for post status counts per platform
    status_rows = await db.execute(
        select(
            SocialAccount.platform,
            Post.status,
            func.count(PostTarget.post_id),
        )
        .join(PostTarget, PostTarget.social_account_id == SocialAccount.id)
        .join(Post, Post.id == PostTarget.post_id)
        .where(SocialAccount.team_id == team.id, Post.created_at >= since)
        .group_by(SocialAccount.platform, Post.status)
    )
    status_by_platform: dict[str, dict] = {}
    for plat, status, cnt in status_rows.all():
        bucket = status_by_platform.setdefault(plat, {"posts": 0, "published": 0, "scheduled": 0})
        bucket["posts"] += cnt
        if status == PostStatus.PUBLISHED:
            bucket["published"] += cnt
        elif status == PostStatus.SCHEDULED:
            bucket["scheduled"] += cnt

    event_rows = await db.execute(
        select(
            SocialAccount.platform,
            AnalyticsEvent.event_type,
            func.sum(_event_count_expr()),
        )
        .join(SocialAccount, SocialAccount.id == AnalyticsEvent.social_account_id)
        .where(
            SocialAccount.team_id == team.id,
            AnalyticsEvent.occurred_at >= since,
        )
        .group_by(SocialAccount.platform, AnalyticsEvent.event_type)
    )
    events_by_platform: dict[str, dict[str, int]] = {}
    for plat, event_type, cnt in event_rows.all():
        events_by_platform.setdefault(plat, {})[event_type] = int(cnt or 0)

    metrics: list[PlatformMetrics] = []
    for platform in platforms_seen:
        counts = status_by_platform.get(platform, {"posts": 0, "published": 0, "scheduled": 0})
        event_counts = events_by_platform.get(platform, {})
        impressions = event_counts.get("impression", 0)
        engagement = _engagement_sum(event_counts)
        metrics.append(
            PlatformMetrics(
                platform=platform,
                posts_count=counts["posts"],
                published_count=counts["published"],
                scheduled_count=counts["scheduled"],
                total_engagement=engagement,
                total_impressions=impressions,
                engagement_rate=_engagement_rate(engagement, impressions),
            )
        )
    return metrics


@router.get("/reports/export")
async def export_report(
    format: str = Query("csv", pattern="^(csv|json)$"),
    days: int = Query(30, ge=1, le=365),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    team = await _team_for_user(db, current_user)
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    since = datetime.now(UTC) - timedelta(days=days)

    impressions_col = func.sum(
        case((AnalyticsEvent.event_type == "impression", _event_count_expr()), else_=0)
    )
    likes_col = func.sum(case((AnalyticsEvent.event_type == "like", _event_count_expr()), else_=0))
    comments_col = func.sum(case((AnalyticsEvent.event_type == "comment", _event_count_expr()), else_=0))
    shares_col = func.sum(case((AnalyticsEvent.event_type == "share", _event_count_expr()), else_=0))
    clicks_col = func.sum(case((AnalyticsEvent.event_type == "click", _event_count_expr()), else_=0))

    result = await db.execute(
        select(
            Post.id,
            SocialAccount.platform,
            Post.status,
            Post.scheduled_at,
            PostTarget.published_at,
            impressions_col,
            likes_col,
            comments_col,
            shares_col,
            clicks_col,
        )
        .join(PostTarget, PostTarget.post_id == Post.id)
        .join(SocialAccount, SocialAccount.id == PostTarget.social_account_id)
        .outerjoin(
            AnalyticsEvent,
            (AnalyticsEvent.post_id == Post.id)
            & (AnalyticsEvent.social_account_id == PostTarget.social_account_id),
        )
        .where(Post.team_id == team.id, Post.created_at >= since)
        .group_by(
            Post.id,
            SocialAccount.platform,
            Post.status,
            Post.scheduled_at,
            PostTarget.published_at,
        )
    )

    rows = []
    for (
        post_id,
        platform,
        status,
        scheduled_at,
        published_at,
        impressions,
        likes,
        comments,
        shares,
        clicks,
    ) in result.all():
        imp = int(impressions or 0)
        like_n = int(likes or 0)
        comment_n = int(comments or 0)
        share_n = int(shares or 0)
        click_n = int(clicks or 0)
        rows.append(
            {
                "post_id": str(post_id),
                "platform": platform,
                "status": status.value if hasattr(status, "value") else str(status),
                "scheduled_at": scheduled_at.isoformat() if scheduled_at else None,
                "published_at": published_at.isoformat() if published_at else None,
                "impressions": imp,
                "likes": like_n,
                "comments": comment_n,
                "shares": share_n,
                "clicks": click_n,
                "engagement_rate": _engagement_rate(
                    like_n + comment_n + share_n + click_n, imp
                ),
            }
        )

    if format == "csv":
        import csv
        import io

        output = io.StringIO()
        if rows:
            writer = csv.DictWriter(output, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        filename = f"analytics_{datetime.now(UTC).strftime('%Y%m%d')}.csv"
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    import json as json_mod

    filename = f"analytics_{datetime.now(UTC).strftime('%Y%m%d')}.json"
    return Response(
        content=json_mod.dumps(rows, indent=2, default=str),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class SyncAnalyticsRequest(BaseModel):
    days: int = 365
    async_mode: bool = True


class SyncAnalyticsResponse(BaseModel):
    status: str
    synced: int = 0
    skipped: int = 0
    errors: list[str] = []
    task_id: str | None = None
    snapshots: list[str] = []


class SnapshotOut(BaseModel):
    id: uuid.UUID
    post_id: uuid.UUID | None
    social_account_id: uuid.UUID
    platform: str
    platform_post_id: str | None
    impressions: int
    clicks: int
    likes: int
    comments: int
    shares: int
    reach: int
    engagement: int
    engagement_rate: float
    source: str
    notes: str | None
    captured_at: datetime
    raw: dict

    class Config:
        from_attributes = True


@router.post("/sync", response_model=SyncAnalyticsResponse)
async def trigger_analytics_sync(
    body: SyncAnalyticsRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Pull LinkedIn (and future platforms) post metrics into Postgres snapshots."""
    body = body or SyncAnalyticsRequest()
    team = await _team_for_user(db, current_user)
    if not team:
        raise HTTPException(status_code=400, detail="No team found")

    if body.async_mode:
        async_result = sync_team_analytics_task.delay(str(team.id), body.days)
        return SyncAnalyticsResponse(status="queued", task_id=str(async_result.id))

    result = await sync_team_analytics(db, team.id, days=body.days)
    return SyncAnalyticsResponse(
        status="completed",
        synced=result.synced,
        skipped=result.skipped,
        errors=result.errors,
        snapshots=result.snapshots,
    )


@router.get("/snapshots", response_model=list[SnapshotOut])
async def list_analytics_snapshots(
    days: int = Query(30, ge=1, le=365),
    post_id: uuid.UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List stored post metric snapshots for further processing / export."""
    team = await _team_for_user(db, current_user)
    if not team:
        return []
    since = datetime.now(UTC) - timedelta(days=days)
    q = (
        select(PostAnalyticsSnapshot)
        .where(
            PostAnalyticsSnapshot.team_id == team.id,
            PostAnalyticsSnapshot.captured_at >= since,
        )
        .order_by(PostAnalyticsSnapshot.captured_at.desc())
        .limit(limit)
    )
    if post_id:
        q = q.where(PostAnalyticsSnapshot.post_id == post_id)
    rows = (await db.execute(q)).scalars().all()
    return rows
